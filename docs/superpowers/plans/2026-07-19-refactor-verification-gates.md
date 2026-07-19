# Refactor Verification Gates

Run focused tests for the touched subsystem first, then run the broader suite before merge.

## Baseline

```bash
uv run python -m compileall app.py blueprints common controller display file_mgmt grillplat notify probes
uv run pytest tests/unit tests/characterization -q
uv run pytest tests/web -q
```

## Before merge

```bash
uv run pytest -q
uv run ruff check .
```

## Ruff baseline

`ruff.toml` currently ignores the legacy rule families discovered during initial adoption so the command above can pass as a merge gate. Remove those ignores incrementally in focused cleanup PRs.
