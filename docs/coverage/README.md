# Coverage measurement

PiFire uses [`pytest-cov`](https://pytest-cov.readthedocs.io/) (built on `coverage.py`)
to measure test coverage of the runtime packages: `blueprints`, `common`, `controller`,
`display`, `file_mgmt`, `grillplat`, `notify`, `probes`.

## Running the combined baseline

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ \
  --cov --cov-report=term-missing --cov-report=html:htmlcov --cov-report=json:coverage.json -q
```

This runs the full test suite (unit, integration, and web/UI tests) and produces:

- A `term-missing` summary printed to the console (per-file and total line/branch %).
- `htmlcov/index.html` — a browsable HTML coverage report. Open it directly in a browser
  to drill into per-file, per-line coverage (covered/missed/partial lines are
  color-coded).
- `coverage.json` — machine-readable coverage data, consumed by downstream tooling
  (e.g. gap-analysis scripts) to identify undertested modules.

Both `coverage.json` and `htmlcov/` are git-ignored (along with `.coverage` and
`.coverage.*`) — they're generated artifacts, not source. Regenerate them locally with
the command above whenever you need current numbers.

## Baseline (2026-07-19/20)

Recorded from the first coverage-enabled run of `tests/` on `test/coverage-tooling`:

- **1570 passed**, 0 skipped (all tiers ran, including chromium-gated web/UI tests).
- Line coverage (statements): **57.9%**
- Branch coverage: **43.4%**
- Combined (coverage.py's default `percent_covered`, statements+branches blended): **54.6%**

## The `concurrency = ["thread"]` gotcha

PiFire's web/UI tests (`tests/web/`) spin up the real Flask app in a **background
thread** via `tests/web/conftest.py`'s `live_server` fixture, then drive it with an HTTP
client (and Playwright for browser-level tests). `coverage.py` does not trace code
running in threads other than the one that started measurement **unless you tell it to**.

Without `concurrency = ["thread"]` in `[tool.coverage.run]` (see `pyproject.toml`), every
route/blueprint module executed only inside that background thread reads as **0%
covered** — even though the tests genuinely exercise it. This silently poisons the
numbers for the entire `blueprints/` package and any code reachable only through an HTTP
request.

`[tool.coverage.run]` also sets `branch = true` (branch coverage, not just line
coverage) and scopes `source` to the runtime packages, excluding `tests/`, `__pycache__`,
and virtualenv directories.

### Validating it's working

After any change to the coverage config, sanity-check that thread coverage is actually
being captured:

```bash
uv run python -c "import json; d=json.load(open('coverage.json'))['files']; k=[f for f in d if 'blueprints/pellets/routes.py' in f][0]; print(k, d[k]['summary']['percent_covered'])"
```

`blueprints/pellets/routes.py` is exercised by the pellets web tests, so this should
print a **nonzero** percentage (baseline: ~84%). If it prints `0.0`, thread coverage is
not being captured — check that:

1. `concurrency = ["thread"]` is present in `[tool.coverage.run]` in `pyproject.toml`.
2. `pytest-cov` (not a bare `coverage run` wrapper) is driving the run, so coverage
   starts before the `live_server` fixture spawns its thread.

## Chromium-gated tests

Some web/UI tests are gated on a Chromium/Playwright browser being available. If
Chromium isn't installed in the environment, that whole tier is skipped by pytest, and
**its coverage is absent from the report** (the code paths it would exercise show as
uncovered, not "skipped"). If you see a suspiciously low `blueprints/` percentage,
check the pytest summary line for a skip count before assuming a regression.
