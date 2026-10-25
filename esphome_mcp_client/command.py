"""Result type shared by every streaming command (dashboard, /ws, device logs)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CommandResult:
    """Outcome of a streaming command: its output lines and exit code."""

    lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    truncated: bool = False
    # For bounded captures: why it ended - "window" (time ran out),
    # "max_lines", "stopped" (stop_event) or "closed" (the stream ended).
    end_reason: str | None = None

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    @property
    def success(self) -> bool:
        return self.exit_code == 0
