"""In-memory registry for long-running ESPHome build jobs.

Slow operations (compile/upload/run) can outlive an MCP client's request
timeout, especially on a first build that downloads a toolchain. Running them
as a background task inside Home Assistant and polling a job id avoids holding a
single tool call open for minutes. This module is deliberately free of Home
Assistant imports so it can be unit-tested directly.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

# Keep the registry bounded.
MAX_JOBS = 50
JOB_TTL_SECONDS = 3600
# Cap the output returned in a status response.
MAX_OUTPUT_LINES = 2000


@dataclass
class Job:
    """A single background build job and its accumulating output."""

    id: str
    kind: str  # compile | upload | run | validate | clean
    configuration: str
    addon_slug: str | None = None
    status: str = "running"  # running | done | error
    exit_code: int | None = None
    lines: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def is_finished(self) -> bool:
        return self.status != "running"

    def to_dict(self, *, max_output_lines: int = MAX_OUTPUT_LINES) -> dict:
        tail = self.lines[-max_output_lines:]
        return {
            "job_id": self.id,
            "kind": self.kind,
            "configuration": self.configuration,
            "addon_slug": self.addon_slug,
            "status": self.status,
            "exit_code": self.exit_code,
            "success": self.status == "done" and self.exit_code == 0,
            "line_count": len(self.lines),
            "output_truncated": self.truncated or len(self.lines) > len(tail),
            "output": "\n".join(tail),
            "error": self.error,
        }


class JobRegistry:
    """Holds active and recently-finished jobs, with bounded retention."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str, configuration: str, addon_slug: str | None) -> Job:
        self._prune()
        job = Job(
            id=uuid.uuid4().hex,
            kind=kind,
            configuration=configuration,
            addon_slug=addon_slug,
        )
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        # Drop finished jobs past their TTL.
        for jid in list(self._jobs):
            job = self._jobs[jid]
            if job.finished_at is not None and now - job.finished_at > JOB_TTL_SECONDS:
                del self._jobs[jid]
        # Cap total count by evicting the oldest finished jobs first.
        if len(self._jobs) > MAX_JOBS:
            finished = sorted(
                (j for j in self._jobs.values() if j.is_finished),
                key=lambda j: j.finished_at or 0.0,
            )
            for job in finished[: len(self._jobs) - MAX_JOBS]:
                self._jobs.pop(job.id, None)
