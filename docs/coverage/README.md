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

## Coverage gate (changed lines)

The global coverage number above (57.9% line / 43.4% branch) is a **legacy baseline**,
not a gate — most of the codebase predates coverage measurement, and requiring the
whole repo to hit some threshold would either block unrelated work or invite gaming the
number. Instead, [`diff-cover`](https://github.com/Bachmann1234/diff-cover) gates only
the lines a branch actually **adds or changes**, comparing against the coverage XML
report and a `git diff` against a base branch.

Run this before merging any branch:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ --cov --cov-report=xml -q
uv run diff-cover coverage.xml --compare-branch=massive-reworks-and-new-ui --fail-under=80
```

- The first command runs the full suite and additionally emits `coverage.xml` (Cobertura
  format), the input `diff-cover` needs — the `--cov-report=json`/`html` reports from the
  baseline command above are for human/tooling consumption and aren't consumed here.
- The second command diffs the current branch against `massive-reworks-and-new-ui`,
  finds the lines that are new or modified, cross-references them against
  `coverage.xml`, and **fails only if fewer than 80% of those changed lines are
  covered** — printing the specific uncovered new lines when it fails.
- This means: legacy code with 0% coverage that you don't touch never blocks you: only
  the lines *you* add or change need tests. The global % isn't enforced by this gate —
  treat it as a trend to watch via `htmlcov/index.html`, not a pass/fail check.
- `coverage.xml` is a regenerated artifact (git-ignored, like `coverage.json`/
  `htmlcov/`) — do not commit it.
- This is a documented pre-merge command, not a `pytest addopts` change: it does not run
  automatically and does not fail the test suite itself. Run it by hand (or wire it into
  CI) before merging.
