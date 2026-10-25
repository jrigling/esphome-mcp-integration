"""Trim ESPHome command output down to what an agent needs.

``esphome config`` (validate) prints the whole resolved configuration on
success, and a build prints hundreds of CMake / ninja / PlatformIO progress
lines. Both bury the few lines that matter. These trimmers keep the meaningful
lines and report how many were dropped; the untrimmed output stays available to
callers that ask for it.

They err on the side of keeping: anything not recognised as noise is kept, every
line from ``Failed config`` onward is kept, and on a failed build the final
lines are kept unfiltered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .logtext import clean_line

# ``esphome``'s own log lines: ``INFO Reading configuration ...`` (optionally
# with a ``[HH:MM:SS]`` prefix).
_CLI_LINE = re.compile(r"^(?:\[[\d:.]+\]\s*)?(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s")
_ERROR_LEVELS = {"ERROR", "CRITICAL"}
_FAILED_CONFIG = "Failed config"

_BUILD_NOISE = [
    re.compile(p)
    for p in (
        r"^\[\d+/\d+\]\s",  # ninja progress
        r"^-- ",  # CMake status
        r"^(Compiling|Archiving|Indexing|Linking|Retrieving|Generating|Scanning) \S",  # PlatformIO / IDF steps
        r"^[─-╿]",  # box-drawing (the rich size table)
        r"^esptool(\.py)? v",
        r"^(Creating \S+ image|Merged \d+ ELF section|Successfully created \S+ image|Generated /)",
        r"^Verbose mode can be enabled",
        r"^\|-- ",  # PlatformIO dependency graph
        r"^ - (framework|tool|toolchain)-",  # PlatformIO package list
    )
]
# A compiler diagnostic: ``path:line:col: error: ...`` (also warning / note).
_DIAGNOSTIC = re.compile(r":\d+(:\d+)?: (fatal error|error|warning|note): ")
# The code excerpt GCC prints under a diagnostic: `` 12 |   foo();`` / ``    |   ^~~``.
_EXCERPT = re.compile(r"^\s*\d*\s*\|")
FAILED_BUILD_TAIL = 40


@dataclass
class TrimmedOutput:
    """Kept lines plus how many cleaned, non-blank lines were dropped."""

    lines: list[str]
    omitted: int

    @property
    def output(self) -> str:
        return "\n".join(self.lines)


def _cleaned(lines: list[str]) -> list[str]:
    """Clean each line; a streamed chunk may itself contain several lines."""
    return [
        line
        for raw in lines
        for line in (clean_line(part) for part in raw.split("\n"))
        if line.strip()
    ]


def _cli_level(line: str) -> str | None:
    match = _CLI_LINE.match(line)
    return match.group(1) if match else None


def trim_validate_output(lines: list[str]) -> TrimmedOutput:
    """Keep ``esphome``'s log lines and diagnostics; drop the config dump.

    Kept: every log line (``INFO`` / ``WARNING`` / ``ERROR`` ...), the details
    that follow an ``ERROR`` (a traceback, a YAML syntax excerpt), and the
    whole ``Failed config`` section. Dropped: the resolved configuration that a
    successful validation prints.
    """
    cleaned = _cleaned(lines)
    kept: list[str] = []
    in_failure = False
    after_error = False
    for line in cleaned:
        if in_failure or line.strip() == _FAILED_CONFIG:
            in_failure = True
            kept.append(line)
            continue
        level = _cli_level(line)
        if level is not None:
            kept.append(line)
            after_error = level in _ERROR_LEVELS
        elif after_error:
            kept.append(line)
    return TrimmedOutput(kept, len(cleaned) - len(kept))


def trim_build_output(lines: list[str], *, failed: bool = False) -> TrimmedOutput:
    """Drop build-progress noise; keep diagnostics, results and anything unknown.

    Compiler diagnostics keep their code excerpt. When ``failed``, the last
    ``FAILED_BUILD_TAIL`` lines are kept unfiltered so no error context is lost.
    """
    cleaned = _cleaned(lines)
    tail_start = max(len(cleaned) - FAILED_BUILD_TAIL, 0) if failed else len(cleaned)
    kept: list[str] = []
    in_excerpt = False
    for index, line in enumerate(cleaned):
        if index >= tail_start:
            kept.append(line)
            continue
        if _DIAGNOSTIC.search(line):
            kept.append(line)
            in_excerpt = True
            continue
        if in_excerpt and _EXCERPT.match(line):
            kept.append(line)
            continue
        in_excerpt = False
        if _cli_level(line) is None and any(p.match(line) for p in _BUILD_NOISE):
            continue
        kept.append(line)
    return TrimmedOutput(kept, len(cleaned) - len(kept))


def clean_output(lines: list[str]) -> TrimmedOutput:
    """Every line, with ANSI codes and redraws removed and nothing dropped."""
    return TrimmedOutput(_cleaned(lines), 0)
