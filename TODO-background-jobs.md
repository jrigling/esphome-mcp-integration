# TODO: Finish the background-jobs feature

The work lives on branch **`wip/background-jobs`** (commit `78f25d9`, a snapshot of
the uncommitted work that was set aside when cutting the `v0.4.0` extra-file-access
release). `main` does **not** contain any of it.

## What the feature does
Lets slow builds (`compile`/`upload`/`run`) run as a Home Assistant background task
that returns a `job_id` immediately, with an `esphome_job_status` tool to poll — so a
slow first build (toolchain download) doesn't exceed the MCP client's request timeout.
Diff vs `main`: ~+225 lines across 4 files.

## What's already done (high quality, on `wip/background-jobs`)
- `custom_components/esphome_mcp_bridge/jobs.py` (NEW, 93 lines) — `Job` + `JobRegistry`
  with TTL pruning, max-job eviction, bounded output. HA-free / unit-testable by design.
- `esphome_mcp_client/dashboard.py` (+19) — threads an `on_line` callback through
  `_run_command` and every build method so output streams into the job live.
- `custom_components/esphome_mcp_bridge/llm.py` (+116) — `_run_job`,
  `_start_background_job`, `background=true` params on compile/upload/run, and the
  `JobStatusTool` class.

## Blockers / remaining work

### 🔴 1. `JobStatusTool` is never registered
It's defined but missing from the `tools` list in `async_get_api_instance` (llm.py).
An agent can start a job and get a `job_id` but has **no tool to poll it** — the feature
is non-functional end-to-end. One-line fix: add `JobStatusTool()` to the list.

### 🔴 2. Client release + manifest pin bump required
`llm.py` calls `client.compile(..., on_line=...)`, but the integration pins
`esphome-mcp-client==0.1.2`, which has no `on_line` param. A real HA install pulls that
pinned wheel from PyPI → every `background=true` call throws `TypeError` at runtime.
Fix (IRREVERSIBLE PyPI publish — get explicit go-ahead first):
1. Bump client version in `pyproject.toml` + `esphome_mcp_client/__init__.py` (0.1.2 → 0.1.3).
2. Tag `client-v0.1.3`, push → `publish.yml` uploads to PyPI.
3. Bump the manifest pin to `esphome-mcp-client==0.1.3`.

### 🟡 3. No tests
- Add `tests/test_jobs.py` (registry TTL/eviction, `to_dict`) — `jobs.py` was written to be
  unit-testable.
- Add an `on_line` callback assertion in `tests/test_dashboard.py`.

### 🟡 4. Version bumps
`v0.4.0` is taken, so the integration release becomes `v0.5.0` (manifest `version`).

### Minor (not blocking)
`_run_job` doesn't pass `max_seconds`, so background builds still inherit the client's
`DEFAULT_BUILD_TIMEOUT` — slightly undercuts the "outlives the timeout" goal. Worth a glance.

## Suggested order
1. Register `JobStatusTool()` in the tool list. (local, reversible)
2. Add `test_jobs.py` + `on_line` assertion in `test_dashboard.py`. (local, reversible)
3. Cut client `0.1.3` → `client-v0.1.3` → PyPI; bump manifest pin. (**needs go-ahead**)
4. Bump integration to `0.5.0`; commit, tag `v0.5.0`, GitHub Release.

Steps 1–2 can be done first and are fully local/reversible; stop before step 3 (PyPI) for sign-off.

---

## ⚠️ Also pending: investigate `v0.4.0` GitHub Actions failures
The `v0.4.0` release (extra-file-access, already published) has failing GitHub Actions.
Diagnose and fix before continuing the jobs work. (See workflows: `test-build.yml`,
`publish.yml`, `smoke-test.yml`.)
