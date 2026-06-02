# PyPI Setup & Release Guide

This repository ships **two** deliverables from one codebase:

| Artifact | What it is | How it's distributed |
|----------|-----------|----------------------|
| `esphome-mcp-client` | Pure-Python async client (Supervisor + ESPHome dashboard transport) | **PyPI** (`pip install esphome-mcp-client`) |
| `esphome_mcp_bridge` | Home Assistant custom integration (LLM tools) | **HACS** / manual copy into `config/custom_components/` |

The integration declares the client as a requirement in its `manifest.json`
(`"requirements": ["esphome-mcp-client==X.Y.Z"]`), so Home Assistant installs
the PyPI package automatically on startup — exactly the same split as
`python-jebao` (PyPI) and `homeassistant-jebao` (HACS).

This guide covers publishing the **`esphome-mcp-client`** package to PyPI.

---

## One-time setup

### 1. Reserve the name / create the PyPI project

The project is created automatically on first upload. Just make sure the
`name` in [`pyproject.toml`](pyproject.toml) (`esphome-mcp-client`) is still
available on https://pypi.org. If it's taken, pick another and update
`pyproject.toml` **and** the integration's `manifest.json` requirement.

### 2. Create a PyPI API token

1. Log in at https://pypi.org → **Account settings** → **API tokens**.
2. **Add API token**. For the very first upload, scope it to *"Entire account"*
   (the project doesn't exist yet to scope to). After the first release,
   delete it and create a new token scoped to the `esphome-mcp-client` project.
3. Copy the token (starts with `pypi-`); you won't see it again.

### 3. Add the token to GitHub

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**:

- **Name:** `PYPI_API_TOKEN`
- **Value:** the `pypi-…` token

The [`publish.yml`](.github/workflows/publish.yml) workflow reads this secret.

---

## Two independent release lines (important)

This repo ships two things with **separate version lines**, and they use
**different release mechanisms** so they don't collide:

| Deliverable | Versioned in | Released by | Consumed by |
|---|---|---|---|
| `esphome-mcp-client` (PyPI) | `pyproject.toml` + `esphome_mcp_client/__init__.py` | pushing a **`client-v*` tag** (or manual workflow run) | the integration's `manifest.json` requirement |
| `esphome_mcp_bridge` (HACS integration) | `custom_components/esphome_mcp_bridge/manifest.json` → `version` | a **GitHub Release** (`vX.Y.Z` tag) | HACS users |

> **Why:** HACS installs the latest *GitHub Release*. If GitHub Releases also
> triggered the PyPI publish (the original setup), every integration release
> would re-run the publish — and, worse, a library release would show up in
> HACS as the integration version. Keeping PyPI on `client-v*` tags and HACS on
> `vX.Y.Z` Releases separates them cleanly.

### Releasing the PyPI client library

1. Bump `pyproject.toml` `version` **and** `esphome_mcp_client/__init__.py`
   `__version__` so they agree. Commit + push to `main`.
2. Tag and push:
   ```bash
   git tag client-v0.1.1 && git push origin client-v0.1.1
   ```
   This triggers [`publish.yml`](.github/workflows/publish.yml) (build →
   `twine check` → `twine upload --skip-existing`). You can also run it manually
   via **Actions → Publish to PyPI → Run workflow**.
3. **Verify:** `pip install esphome-mcp-client==<new version>`.
4. If the integration needs the new client, bump its `manifest.json`
   `requirements` pin and cut an integration release (below).

### Releasing the HACS integration

1. Bump `custom_components/esphome_mcp_bridge/manifest.json` → `version`.
   Commit + push to `main`.
2. **Create a GitHub Release** (Releases → Draft a new release): tag `vX.Y.Z`
   matching the manifest version, target `main`, publish. This does **not**
   trigger the PyPI workflow. HACS users then see the update.

---

## Local build / publish (manual fallback)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,build]"

# Test & lint
ruff check esphome_mcp_client tests
pytest

# Build
python -m build
twine check dist/*

# Upload (uses ~/.pypirc or prompts; or set TWINE_USERNAME=__token__)
twine upload dist/*
```

> Tip: test against TestPyPI first with
> `twine upload --repository testpypi dist/*` and
> `pip install --index-url https://test.pypi.org/simple/ esphome-mcp-client`.

---

## Versioning notes

- The integration pins an **exact** client version (`==`) so a Home Assistant
  install always pulls a known-good client. After publishing a new client,
  bump the pin in `manifest.json` in a follow-up commit.
- Follow semver: patch for fixes, minor for new tools/endpoints, major for
  breaking client API changes.
