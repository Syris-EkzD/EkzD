from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Mapping, TextIO

ALT_SCREEN_ENTER = "\x1b[?1049h"
ALT_SCREEN_EXIT = "\x1b[?1049l"
CLEAR_SCREEN = "\x1b[2J"
CURSOR_HOME = "\x1b[H"
STYLE_RESET = "\x1b[0m"
MIN_PERSISTENT_COLUMNS = 72
MIN_PERSISTENT_LINES = 18


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

    def __enter__(self) -> "TerminalSession":
        self._entered = True
        if self.capabilities.alternate_screen:
            self.output.write(ALT_SCREEN_ENTER + CLEAR_SCREEN + CURSOR_HOME)
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
                self.output.write(STYLE_RESET + ALT_SCREEN_EXIT)
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
        available_history = max(1, self.capabilities.height - fixed_lines)
        history = self._history[-available_history:]
        hidden = len(self._history) - len(history)
        if hidden > 0 and available_history > 1:
            history = [f"… {hidden} earlier line{'s' if hidden != 1 else ''} hidden …", *history[-(available_history - 1):]]

        parts = [*header_lines, "", *history, "", *action_lines]
        frame = "\n".join(parts) + "\n"
        self.output.write(CLEAR_SCREEN + CURSOR_HOME + frame)
        self.output.flush()
