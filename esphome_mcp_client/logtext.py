"""Text helpers for ESPHome console output: ANSI stripping, log-line parsing,
and a streaming log filter.

ESPHome log lines look like ``[D][sensor:104]: 'Temp': Sending state 21.5`` -
a level letter (``E W I C D V VV``), the tag and source line, and on
multi-threaded platforms an optional ``[thread]`` before the colon. The device
wraps the header in ANSI colour codes; ``esphome logs`` (what the dashboard
streams) additionally prefixes a ``[HH:MM:SS.mmm]`` timestamp and, in dashboard
mode, writes ESC as the four literal characters ``\\033``. Continuation lines of
a multi-line message start with whitespace.
"""
from __future__ import annotations

import fnmatch
import re
from collections import deque
from dataclasses import dataclass, field

# Same pattern aioesphomeapi uses for real escape sequences.
_ANSI_ESCAPE = re.compile(
    r"(?:\x1B[@-Z\\-_]|[\x80-\x9A\x9C-\x9F]|(?:\x1B\[|\x9B)[0-?]*[ -/]*[@-~])"
)
# ``esphome`` in dashboard mode prints ESC as a literal backslash-zero-three-three.
_LITERAL_ESC = re.compile(r"\\033|\\x1[bB]")

_LOG_LINE = re.compile(
    r"^(?:\[(?P<time>\d{1,2}:\d{2}:\d{2}(?:\.\d{1,6})?)\])?"
    r"\[(?P<level>VV|[EWICDV])\]"
    r"\[(?P<tag>[^\]:]+)(?::(?P<lineno>\d+))?\]"
    r"(?:\[(?P<thread>[^\]]*)\])?"
    r":\s?(?P<message>.*)$"
)

# Severity order matches the native API's LogLevel enum.
LEVELS: dict[str, int] = {
    "error": 1,
    "warn": 2,
    "info": 3,
    "config": 4,
    "debug": 5,
    "verbose": 6,
    "very_verbose": 7,
}
_LETTER_LEVELS = {"E": 1, "W": 2, "I": 3, "C": 4, "D": 5, "V": 6, "VV": 7}
CONFIG_LEVEL = LEVELS["config"]
SEPARATOR = "--"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences, including the literal ``\\033`` form."""
    if "\\" in text:
        text = _LITERAL_ESC.sub("\x1b", text)
    return _ANSI_ESCAPE.sub("", text)


def clean_line(line: str) -> str:
    """Strip ANSI codes and carriage-return redraws from one console line.

    A terminal progress line rewritten with ``\\r`` keeps only its final state.
    """
    line = strip_ansi(line).rstrip("\r\n")
    if "\r" in line:
        line = line.rsplit("\r", 1)[-1]
    return line.rstrip()


@dataclass(frozen=True)
class LogEntry:
    """A parsed ESPHome log line."""

    level: int
    tag: str
    message: str
    line: int | None = None
    thread: str | None = None
    time: str | None = None


def parse_log_line(line: str) -> LogEntry | None:
    """Parse an already-cleaned log line, or return ``None`` if it isn't one."""
    match = _LOG_LINE.match(line)
    if match is None:
        return None
    lineno = match.group("lineno")
    return LogEntry(
        level=_LETTER_LEVELS[match.group("level")],
        tag=match.group("tag"),
        message=match.group("message"),
        line=int(lineno) if lineno else None,
        thread=match.group("thread"),
        time=match.group("time"),
    )


def compile_search(search: str) -> re.Pattern[str]:
    """``/regex/`` (``/regex/i`` for case-insensitive) or a case-insensitive
    substring. Raises ``re.error`` for an invalid regex."""
    if len(search) >= 3 and search.startswith("/"):
        body, slash, flags = search[1:].rpartition("/")
        if slash and body and set(flags) <= {"i"}:
            return re.compile(body, re.IGNORECASE if flags else 0)
    return re.compile(re.escape(search), re.IGNORECASE)


@dataclass
class LogFilter:
    """Streaming filter over ESPHome log lines.

    Feed raw lines one at a time with :meth:`feed`; it returns the cleaned lines
    to keep. An entry is kept when it is at or above ``min_level``, its tag
    passes ``tags`` / ``exclude_tags`` (``fnmatch`` globs), it is not part of
    the config dump (``[C]`` lines) unless ``include_config_dump``, and it
    matches ``search`` when one is given. With ``context``, up to that many
    otherwise-eligible lines before and after each match are kept too, and
    ``--`` separates groups that aren't contiguous, as with ``grep -C``.

    Continuation lines (leading whitespace) follow their entry. Lines that are
    neither entries nor continuations - ``esphome logs``' own ``INFO Starting
    log output ...`` / ``WARNING Disconnected ...`` - describe the capture
    itself and are always kept.
    """

    min_level: int = LEVELS["very_verbose"]
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    search: str | None = None
    context: int = 0
    include_config_dump: bool = False

    def __post_init__(self) -> None:
        self.lines_received = 0
        self.lines_returned = 0
        self._pattern = compile_search(self.search) if self.search else None
        # Leading context: held entries, each with its continuation lines.
        self._before: deque[list[str]] = deque()
        self._after = 0
        # Decision for the current entry, inherited by its continuation lines:
        # "keep", "buffer" (eligible, held as possible context) or "drop".
        self._state = "drop"
        self._seen_entry = False
        self._discarded = False

    def describe(self) -> dict:
        """The filters in effect, for reporting back to the caller."""
        level = next(k for k, v in LEVELS.items() if v == self.min_level)
        described: dict = {"level": level, "include_config_dump": self.include_config_dump}
        if self.tags:
            described["tags"] = list(self.tags)
        if self.exclude_tags:
            described["exclude_tags"] = list(self.exclude_tags)
        if self.search:
            described["search"] = self.search
            described["context"] = self.context
        return described

    def _eligible(self, entry: LogEntry) -> bool:
        if entry.level > self.min_level:
            return False
        if entry.level == CONFIG_LEVEL and not self.include_config_dump:
            return False
        if self.tags and not any(fnmatch.fnmatchcase(entry.tag, p) for p in self.tags):
            return False
        return not any(fnmatch.fnmatchcase(entry.tag, p) for p in self.exclude_tags)

    def _emit(self, out: list[str], line: str) -> None:
        if self._discarded and self.context and self.lines_returned:
            out.append(SEPARATOR)
        self._discarded = False
        out.append(line)
        self.lines_returned += 1

    def _hold(self, line: str, *, continuation: bool = False) -> None:
        """Keep an eligible non-matching entry as possible leading context."""
        if not self.context:
            self._discarded = True
            return
        if continuation:
            self._before[-1].append(line)
            return
        self._before.append([line])
        if len(self._before) > self.context:
            self._before.popleft()
            self._discarded = True

    def feed(self, raw: str) -> list[str]:
        """Process one raw line; return the cleaned lines to keep (maybe none)."""
        self.lines_received += 1
        line = clean_line(raw)
        if not line.strip():
            return []
        out: list[str] = []

        entry = parse_log_line(line)
        if entry is None:
            if line[0].isspace() and self._seen_entry:
                if self._state == "keep":
                    self._emit(out, line)
                elif self._state == "buffer":
                    self._hold(line, continuation=True)
            else:
                self._emit(out, line)
            return out

        self._seen_entry = True
        if not self._eligible(entry):
            self._state = "drop"
        elif self._pattern is None or self._pattern.search(line):
            for group in self._before:
                for held in group:
                    self._emit(out, held)
            self._before.clear()
            self._emit(out, line)
            self._state = "keep"
            self._after = self.context
        elif self._after > 0:
            self._emit(out, line)
            self._state = "keep"
            self._after -= 1
        else:
            self._state = "buffer"
            self._hold(line)
        return out
