"""Tests for the background-job registry.

``jobs.py`` is deliberately free of Home Assistant imports, so it is loaded
directly from its file here (bypassing the package ``__init__``, which pulls in
Home Assistant) and exercised in the normal no-HA unit run.
"""
import importlib.util
import sys
import time
from pathlib import Path

_JOBS_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "esphome_mcp_bridge"
    / "jobs.py"
)
_spec = importlib.util.spec_from_file_location("esphome_mcp_jobs", _JOBS_PATH)
jobs = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve the module via sys.modules.
sys.modules[_spec.name] = jobs
_spec.loader.exec_module(jobs)


def test_create_and_get_roundtrip():
    reg = jobs.JobRegistry()
    job = reg.create("compile", "kitchen.yaml", "5c53de3b_esphome")
    assert reg.get(job.id) is job
    assert job.status == "running"
    assert job.is_finished is False
    assert reg.get("nonexistent") is None


def test_to_dict_success_and_output():
    job = jobs.Job(id="x", kind="compile", configuration="kitchen.yaml")
    job.lines = ["Compiling...", "Done"]
    job.exit_code = 0
    job.status = "done"
    d = job.to_dict()
    assert d["job_id"] == "x"
    assert d["success"] is True
    assert d["output"] == "Compiling...\nDone"
    assert d["line_count"] == 2
    assert d["output_truncated"] is False


def test_to_dict_nonzero_exit_is_not_success():
    job = jobs.Job(id="x", kind="run", configuration="k.yaml", status="done", exit_code=1)
    assert job.to_dict()["success"] is False


def test_to_dict_caps_output_and_flags_truncation():
    job = jobs.Job(id="x", kind="compile", configuration="k.yaml", status="done", exit_code=0)
    job.lines = [str(n) for n in range(10)]
    d = job.to_dict(max_output_lines=3)
    assert d["output"] == "7\n8\n9"  # only the tail is returned
    assert d["output_truncated"] is True
    assert d["line_count"] == 10  # full count still reported


def test_prune_drops_finished_jobs_past_ttl():
    reg = jobs.JobRegistry()
    job = reg.create("compile", "k.yaml", None)
    job.status = "done"
    job.finished_at = 1000.0
    # Well past the TTL relative to finished_at.
    reg._prune(now=1000.0 + jobs.JOB_TTL_SECONDS + 1)
    assert reg.get(job.id) is None


def test_prune_keeps_running_jobs_regardless_of_age():
    reg = jobs.JobRegistry()
    job = reg.create("compile", "k.yaml", None)
    # No finished_at: a long-running job must never be pruned by age.
    reg._prune(now=job.created_at + jobs.JOB_TTL_SECONDS * 10)
    assert reg.get(job.id) is job


def test_eviction_caps_total_by_evicting_oldest_finished():
    reg = jobs.JobRegistry()
    # Recent finished_at values (within the TTL window so the TTL pass leaves
    # them) that are ascending, so age ordering drives count-based eviction.
    base = time.time()
    created = []
    for i in range(jobs.MAX_JOBS + 5):
        job = reg.create("compile", f"dev{i}.yaml", None)
        job.status = "done"
        job.finished_at = base + i  # ascending: lower i == older
        created.append(job)
    # Force a final prune still inside the TTL window.
    reg._prune(now=base + jobs.MAX_JOBS + 5)
    assert len(reg._jobs) <= jobs.MAX_JOBS
    # The very oldest finished jobs are the ones evicted; newest retained.
    assert reg.get(created[0].id) is None
    assert reg.get(created[-1].id) is not None
