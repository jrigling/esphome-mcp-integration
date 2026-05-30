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

## Releasing a new version

1. **Bump the version in two or three places so they agree:**
   - `pyproject.toml` → `version`
   - `esphome_mcp_client/__init__.py` → `__version__`
   - If the integration needs the new client, also bump
     `custom_components/esphome_mcp_bridge/manifest.json` →
     `requirements` pin **and** its own `version`.

2. **Commit and push** to `main`. The `test-build` workflow lints, tests, and
   does a trial build on every push/PR.

3. **Create a GitHub Release** (Releases → Draft a new release):
   - Tag: `v0.1.0` (match the version)
   - Publish.

   Publishing the release triggers [`publish.yml`](.github/workflows/publish.yml),
   which builds the sdist + wheel, runs `twine check`, and uploads to PyPI.
   You can also run it manually via **Actions → Publish to PyPI → Run workflow**.

4. **Verify:** `pip install esphome-mcp-client==<new version>` once it appears.

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
