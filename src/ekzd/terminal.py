from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from typing import Mapping, TextIO

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - unsupported platform fallback
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

ALT_SCREEN_ENTER = "\x1b[?1049h"
ALT_SCREEN_EXIT = "\x1b[?1049l"
CLEAR_SCREEN = "\x1b[2J"
CURSOR_HOME = "\x1b[H"
STYLE_RESET = "\x1b[0m"
ALT_SCROLL_SAVE = "\x1b[?1007s"
ALT_SCROLL_DISABLE = "\x1b[?1007l"
ALT_SCROLL_RESTORE = "\x1b[?1007r"
MIN_PERSISTENT_COLUMNS = 72
MIN_PERSISTENT_LINES = 18

KEY_UP = "up"
KEY_DOWN = "down"
KEY_PAGE_UP = "page_up"
KEY_PAGE_DOWN = "page_down"
KEY_HOME = "home"
KEY_END = "end"
KEY_COPY = "copy"
KEY_RETURN = "return"

_KEY_SEQUENCES = {
    "\x1b[A": KEY_UP,
    "\x1bOA": KEY_UP,
    "\x1b[B": KEY_DOWN,
    "\x1bOB": KEY_DOWN,
    "\x1b[5~": KEY_PAGE_UP,
    "\x1b[6~": KEY_PAGE_DOWN,
    "\x1b[H": KEY_HOME,
    "\x1bOH": KEY_HOME,
    "\x1b[1~": KEY_HOME,
    "\x1b[F": KEY_END,
    "\x1bOF": KEY_END,
    "\x1b[4~": KEY_END,
}


def _normalize_key(decoded: str) -> str:
    if decoded in {"\r", "\n", "q", "Q", "\x1b"}:
        return KEY_RETURN
    if decoded in {"c", "C"}:
        return KEY_COPY
    if decoded in {"k", "K"}:
        return KEY_UP
    if decoded in {"j", "J"}:
        return KEY_DOWN
    if decoded in {"u", "U"}:
        return KEY_PAGE_UP
    if decoded in {"d", "D", " "}:
        return KEY_PAGE_DOWN
    if decoded == "g":
        return KEY_HOME
    if decoded == "G":
        return KEY_END
    return _KEY_SEQUENCES.get(decoded, decoded)


def copy_text_to_clipboard(text: str, *, env: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if env is None else env
    candidates: list[list[str]] = []

    is_wayland = bool(environment.get("WAYLAND_DISPLAY")) or environment.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    if is_wayland:
        wl_copy = shutil.which("wl-copy")
        if wl_copy:
            candidates.append([wl_copy])

    if environment.get("DISPLAY"):
        xclip = shutil.which("xclip")
        if xclip:
            candidates.append([xclip, "-selection", "clipboard"])
        xsel = shutil.which("xsel")
        if xsel:
            candidates.append([xsel, "--clipboard", "--input"])

    payload = text.encode("utf-8")
    for command in candidates:
        try:
            result = subprocess.run(
                command,
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


@dataclass(frozen=True)
class TerminalCapabilities:
    screen_control: bool
    alternate_screen: bool
    width: int
    height: int


def detect_terminal_capabilities(
    stream: TextIO,
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    size: os.terminal_size | None = None,
) -> TerminalCapabilities:
    environment = os.environ if env is None else env
    platform_name = os.name if platform_name is None else platform_name
    if size is None:
        size = shutil.get_terminal_size(fallback=(80, 24))

    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    term = environment.get("TERM", "")
    suitable_size = size.columns >= MIN_PERSISTENT_COLUMNS and size.lines >= MIN_PERSISTENT_LINES
    screen_control = is_tty and platform_name == "posix" and term not in {"", "dumb"} and suitable_size
    return TerminalCapabilities(
        screen_control=screen_control,
        alternate_screen=screen_control,
        width=size.columns,
        height=size.lines,
    )


class TerminalSession:
    def __init__(
        self,
        output: TextIO | None = None,
        *,
        capabilities: TerminalCapabilities | None = None,
        history_limit: int = 400,
    ) -> None:
        self.output = sys.stdout if output is None else output
        self.capabilities = capabilities or detect_terminal_capabilities(self.output)
        self.history_limit = max(1, history_limit)
        self._history: list[str] = []
        self._entered = False

    @property
    def persistent(self) -> bool:
        return self.capabilities.screen_control

    @property
    def width(self) -> int:
        return self.capabilities.width

    @property
    def height(self) -> int:
        return self.capabilities.height

    def __enter__(self) -> "TerminalSession":
        self._entered = True
        if self.capabilities.alternate_screen:
            self.output.write(
                ALT_SCROLL_SAVE
                + ALT_SCROLL_DISABLE
                + ALT_SCREEN_ENTER
                + CLEAR_SCREEN
                + CURSOR_HOME
            )
            self.output.flush()
        elif self.capabilities.screen_control:
            self.output.write(CLEAR_SCREEN + CURSOR_HOME)
            self.output.flush()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self._entered:
            return False
        try:
            if self.capabilities.alternate_screen:
                self.output.write(STYLE_RESET + ALT_SCROLL_RESTORE + ALT_SCREEN_EXIT)
            elif self.capabilities.screen_control:
                self.output.write(STYLE_RESET + "\n")
            self.output.flush()
        finally:
            self._entered = False
        return False

    def append(self, text: str) -> None:
        lines = text.rstrip("\n").splitlines()
        if not lines and text:
            lines = [""]
        self._history.extend(lines)
        if len(self._history) > self.history_limit:
            del self._history[: len(self._history) - self.history_limit]

    def redraw(self, header: str, actions: str) -> None:
        if not self.persistent:
            return
        header_lines = header.rstrip("\n").splitlines()
        action_lines = actions.rstrip("\n").splitlines()
        fixed_lines = len(header_lines) + len(action_lines) + 3
        available_history = max(0, self.capabilities.height - fixed_lines)
        history = self._history[-available_history:] if available_history else []
        hidden = len(self._history) - len(history)
        if hidden > 0 and available_history > 1:
            history = [
                f"… {hidden} earlier line{'s' if hidden != 1 else ''} hidden …",
                *history[-(available_history - 1):],
            ]

        parts = [*header_lines, "", *history, "", *action_lines]
        frame = "\n".join(parts) + "\n"
        self.output.write(CLEAR_SCREEN + CURSOR_HOME + frame)
        self.output.flush()

    def _document_lines(self, text: str, content_width: int) -> list[str]:
        wrapper = textwrap.TextWrapper(
            width=max(1, content_width),
            expand_tabs=True,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        )
        lines: list[str] = []
        source_lines = text.splitlines()
        if not source_lines:
            return [""]
        for line in source_lines:
            if not line:
                lines.append("")
                continue
            wrapped = wrapper.wrap(line)
            lines.extend(wrapped or [""])
        return lines

    def redraw_document(
        self,
        title: str,
        text: str,
        offset: int,
        *,
        feedback: str | None = None,
    ) -> tuple[int, int, int]:
        if not self.persistent:
            return 0, 1, 0

        card_width = max(4, self.width - 1)
        content_width = max(1, card_width - 4)
        viewport_height = max(1, self.height - 6)
        lines = self._document_lines(text, content_width)
        total = len(lines)
        max_offset = max(0, total - viewport_height)
        offset = min(max(0, offset), max_offset)
        visible = lines[offset : offset + viewport_height]

        title_text = f"EkzD · {title}"
        prefix = f"┌─ {title_text} "
        top = prefix + "─" * max(0, card_width - len(prefix) - 1) + "┐"
        rows = [f"│ {line:<{content_width}} │" for line in visible]
        rows.extend(f"│ {'':<{content_width}} │" for _ in range(viewport_height - len(visible)))

        feedback_text = feedback or ""
        if len(feedback_text) > content_width:
            feedback_text = feedback_text[: max(1, content_width - 1)] + "…"
        rows.append(f"│ {feedback_text:<{content_width}} │")

        first = 0 if total == 0 else offset + 1
        last = min(total, offset + viewport_height)
        position = f"{first}–{last} / {total}"
        rows.append(f"│ {position:>{content_width}} │")
        bottom = "└" + "─" * max(0, card_width - 2) + "┘"
        controls = "↑/↓ · PgUp/PgDn · Home/End · C copy all · Enter/Esc/q return"
        frame = "\n".join([top, *rows, bottom, controls]) + "\n"
        self.output.write(CLEAR_SCREEN + CURSOR_HOME + frame)
        self.output.flush()
        return offset, viewport_height, total

    def read_key(self, stream: TextIO | None = None) -> str:
        stream = sys.stdin if stream is None else stream
        if (
            os.name != "posix"
            or termios is None
            or tty is None
            or not bool(getattr(stream, "isatty", lambda: False)())
        ):
            value = stream.readline()
            if value == "":
                raise EOFError
            return _normalize_key(value.rstrip("\r\n"))

        try:
            fd = stream.fileno()
        except (AttributeError, OSError, ValueError):
            value = stream.readline()
            if value == "":
                raise EOFError
            return _normalize_key(value.rstrip("\r\n"))

        previous = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            first = os.read(fd, 1)
            if not first:
                raise EOFError
            if first == b"\x03":
                raise KeyboardInterrupt
            if first == b"\x04":
                raise EOFError
            sequence = first
            if first == b"\x1b":
                while len(sequence) < 8 and select.select([fd], [], [], 0.02)[0]:
                    sequence += os.read(fd, 1)
            decoded = sequence.decode(errors="ignore")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

        return _normalize_key(decoded)
