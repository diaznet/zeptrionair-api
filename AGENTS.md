# AGENTS.md: zeptrionair-api

Async Python client library (and `zeptrion` CLI) for Feller zeptrionAir zApps. Used by the Home Assistant integration [`ha-zeptrionair`](https://github.com/diaznet/ha-zeptrionair) (sibling repo `../ha-zeptrionair`), which pins an exact version.

## Where things are

- `docs/protocol.md`: the protocol reference and the **single source of truth** for device behaviour, measured on real hardware. Before you change behaviour, read the relevant section. Update it whenever a measurement adds or corrects a fact, and mark facts not confirmed on hardware as such.
- `src/zeptrionair_api/device.py`: `ZeptrionDevice`, covering HTTP, the push stream (`/zrap/ws`, `chnotify` fallback) and retries. `smartbutton.py` handles Smartbutton programmes and the 730-byte limit. `parsing.py` parses the XML.
- `src/zeptrionair_api/testing/`: `FakeZeptrionDevice`, a simulated zApp that the integration's tests depend on. It reproduces the real cover behaviour (end stops, Feller's two-run scene replay, slats, hold-to-move keys) with a `time_scale`. A change to its behaviour is a change to the public API.
- `tests/fixtures/`: responses captured from real devices with `zeptrion dump`. Anonymise them before committing (rules in `tests/fixtures/README.md`).

## Commands

```powershell
python -m uv sync
python -m uv run pytest --cov
python -m uv run ruff check . ; python -m uv run ruff format --check . ; python -m uv run mypy src
```

CI (`.github/workflows/ci.yml`) runs these on Python 3.12–3.14 on Linux and Windows. Python 3.12 is the minimum supported version, so code must run on it.

The shell tool rejects long inline scripts. Write a Python script to a scratch directory outside the repo and run it there.

## Writing code

- Before writing code against a dependency's API (aiohttp, zeroconf, click …), look it up with Context7 (`npx ctx7@latest library …`, then `docs …`).
- Public API changes go into `CHANGELOG.md` under `[Unreleased]` and the README's library section. Then check that `ha-zeptrionair` still works with them.

## Releasing

The version comes from the git tag (hatch-vcs).

1. Move `[Unreleased]` in `CHANGELOG.md` to the new version, push `main`, and wait for CI to go green.
2. Tag `vX.Y.Z` (annotated) and push the tag. Pre-release tags (`vX.Y.ZrcN`) go to TestPyPI only.
3. The Release workflow publishes to TestPyPI and then waits in the `pypi` environment. Approve it with `gh api repos/diaznet/zeptrionair-api/actions/runs/<id>/pending_deployments` and check that the version is on PyPI.
4. Then release `ha-zeptrionair` with the new pin (see its AGENTS.md).

## Guardrails

- Committed files (code, fixtures, docs, commit messages) use example identifiers only: `192.168.1.50`, `zapp-12345678`, serials such as `99990001`. Pass real IPs to live checks through env vars or the command line.
- Credit third-party projects only where the README already does. A fact known only from an unconfirmed third-party source is written as **[UNVERIFIED]**, without attribution.
- Hardware: only move covers the user has approved, only in daylight, and tell the user before each run. Leave them fully open afterwards. Programming a Smartbutton erases its old programme, so only program the one the user names.
- The user has allowed commits, pushes and tags. A tag needs green CI on its commit.
