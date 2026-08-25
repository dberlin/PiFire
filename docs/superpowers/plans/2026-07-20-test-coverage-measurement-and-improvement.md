# Test Coverage Measurement & Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish real (line + branch) coverage measurement for PiFire, turn the numbers into a risk-ranked gap list, raise coverage on the highest-value gaps, and add a gate that stops new code from regressing coverage.

**Architecture:** Coverage is currently *unmeasured* — `pytest-cov` is not installed and there is no coverage config; all prior "coverage" reasoning was anecdotal test-counting. This plan adds `coverage.py`/`pytest-cov` with **branch** coverage across all runtime packages, captures a combined baseline over the whole `tests/` tree (unit + web + characterization + ui), analyzes the JSON report programmatically into a ranked gap doc, then improves coverage TDD-style on ranked targets and locks in a diff-based gate so the number only goes up.

**Tech Stack:** Python 3.14, pytest, pytest-cov / coverage.py (branch mode), Flask + Flask-SocketIO (web tests run the real app in a background thread via `tests/web/conftest.py`'s `live_server`), pytest-playwright (ui/web chromium tests, skip cleanly when chromium absent), uv, ruff.

## Global Constraints

- Interpreter for EVERY test/coverage run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest ...` (bare `python` gives false failures; the venv has PySide6).
- Run `uvx ruff format` on changed files before every commit; commit with `git commit -F <file>` (zsh eats backticks in `-m`).
- Do NOT lower any existing assertion or delete a test to make a number look better. Coverage is a means; behavior tests are the end.
- Chromium-gated tests may skip cleanly — a skip is not a failure, but note in reports when a whole tier skipped (coverage from that tier is then absent).
- Runtime packages in scope for coverage: `blueprints common controller display file_mgmt grillplat notify probes` (~37k LOC total; largest: `display` ~10.9k, `controller` ~6.0k, `blueprints` ~5.6k, `common` ~5.2k).
- Never introduce a coverage gate that fails the whole legacy suite — gate NEW/changed lines, keep the global number as a non-blocking trend.

---

### Task 1: Add coverage tooling and capture the combined baseline

**Files:**
- Modify: `pyproject.toml` (add dev dep + `[tool.coverage.*]` config)
- Create: `docs/coverage/README.md` (how to run + interpret)
- Produces (artifacts, git-ignored): `coverage.json`, `htmlcov/`

**Interfaces:**
- Produces: a repeatable command that emits `coverage.json` (consumed by Task 2) and a terminal `term-missing` summary.

- [ ] **Step 1: Add `pytest-cov` to the dev dependency group**

In `pyproject.toml` `[dependency-groups].dev`, add:
```toml
    "pytest-cov>=5.0.0",
```
Then `uv sync` and confirm: `uv run python -c "import pytest_cov, coverage; print(pytest_cov.__version__, coverage.__version__)"`.

- [ ] **Step 2: Add coverage config to `pyproject.toml`**

```toml
[tool.coverage.run]
branch = true
concurrency = ["thread"]          # web/ui tests run the app in a background thread; without this the route code reads as 0%
source = ["blueprints", "common", "controller", "display", "file_mgmt", "grillplat", "notify", "probes"]
omit = ["tests/*", "*/__pycache__/*", "*/.venv/*", "*/venv/*"]

[tool.coverage.report]
show_missing = true
skip_covered = false
precision = 1
exclude_also = [
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "def __repr__",
]
```

- [ ] **Step 3: Run the combined baseline**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ \
  --cov --cov-report=term-missing --cov-report=html:htmlcov --cov-report=json:coverage.json -q
```
Expected: the suite passes (currently 1571 passed) and `coverage.json` + `htmlcov/index.html` are written. Record the reported total line% and branch%.

- [ ] **Step 4: VALIDATE thread coverage actually captured the web layer**

This is the critical gotcha. Run:
```bash
uv run python -c "import json; d=json.load(open('coverage.json'))['files']; k=[f for f in d if 'blueprints/pellets/routes.py' in f][0]; print(k, d[k]['summary']['percent_covered'])"
```
Expected: a NONZERO percent (the pellets web tests exercise that file). If it reads `0.0`, `concurrency=["thread"]` is not taking effect — troubleshoot before proceeding: confirm coverage starts before `live_server`'s thread, and that pytest-cov (not a stray `coverage run`) is driving it. A wrong baseline here poisons every later task.

- [ ] **Step 5: Git-ignore the artifacts**

Append to `.gitignore` (if not already ignored): `coverage.json`, `htmlcov/`, `.coverage`, `.coverage.*`.

- [ ] **Step 6: Write `docs/coverage/README.md`**

Document: the exact run command (Step 3), that `concurrency=["thread"]` is required for web-layer coverage, that chromium-skipped tiers drop their coverage, and how to open `htmlcov/index.html`.

- [ ] **Step 7: Commit**

```bash
uvx ruff format pyproject.toml 2>/dev/null; git add pyproject.toml uv.lock .gitignore docs/coverage/README.md
git commit -F <msgfile>   # "test: add branch coverage measurement (pytest-cov)"
```

---

### Task 2: Turn the baseline into a risk-ranked gap report

**Files:**
- Create: `scripts/coverage_gaps.py` (parses `coverage.json` → ranked markdown)
- Create: `docs/coverage/gap-report-2026-07-20.md` (generated output, committed as the snapshot)

**Interfaces:**
- Consumes: `coverage.json` from Task 1.
- Produces: `docs/coverage/gap-report-<date>.md` — a table ranked by `risk_weight × uncovered_lines`, and the top-N modules list that Task 3 draws targets from.

- [ ] **Step 1: Write `scripts/coverage_gaps.py`**

It must: load `coverage.json`; for each file compute `line%`, `branch%` (from `summary`), `missing_lines` count, `missing_branches` count; assign a `risk_weight` by path prefix; emit a markdown table sorted by `risk_weight * (missing_lines + missing_branches)` descending; and print the top 20.

```python
import json, sys

# Risk weights: safety/control/hardware-command code first, then web, then rendering, then pure helpers.
RISK = [
    ("controller/", 5),
    ("common/api_commands", 5),
    ("grillplat/", 5),
    ("notify/", 4),
    ("common/", 3),
    ("blueprints/", 3),
    ("probes/", 3),
    ("file_mgmt/", 3),
    ("display/", 2),
]


def weight(path):
    for prefix, w in RISK:
        if prefix in path:
            return w
    return 1


data = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
rows = []
for path, f in data["files"].items():
    s = f["summary"]
    missing = s.get("missing_lines", 0) + s.get("missing_branches", 0)
    rows.append(
        (
            weight(path) * missing,
            weight(path),
            path,
            s["percent_covered"],
            s.get("missing_lines", 0),
            s.get("missing_branches", 0),
        )
    )
rows.sort(reverse=True)
print("| Rank | File | Risk | Line% | Missing lines | Missing branches | Score |")
print("|---|---|---|---|---|---|---|")
for i, (score, w, path, pct, ml, mb) in enumerate(rows[:40], 1):
    print(f"| {i} | `{path}` | {w} | {pct:.1f} | {ml} | {mb} | {score} |")
```

- [ ] **Step 2: Generate the gap report**

```bash
uv run python scripts/coverage_gaps.py coverage.json > docs/coverage/gap-report-2026-07-20.md
```
Read it. Sanity-check the ranking against intuition (e.g. `controller/` runtime and `common/api_commands.py` should surface high if under-covered; a fully-covered small helper should not).

- [ ] **Step 3: Annotate the report with a "why it matters / test approach" note for the top 10**

For each of the top 10 files, add one line: what kind of tests it needs (unit-pure, web-route characterization, controller golden, hardware-neutralized). This becomes the work-list for Task 3+.

- [ ] **Step 4: Commit** (`scripts/coverage_gaps.py` + the report). Message: "test: add coverage gap analysis + baseline gap report".

---

### Task 3: Raise coverage on the #1 ranked gap (repeatable pattern)

This task is the reusable loop; repeat it per top-ranked module until the risk-weighted gap flattens. The example below shows the method on a representative, self-contained target (`file_mgmt/media.py` — image asset handling, real branching, historically thin coverage). **Substitute the actual #1 target from Task 2's report; keep the loop identical.**

**Files:**
- Test: `tests/unit/file_mgmt/test_media.py` (create or extend)
- Read-only: `file_mgmt/media.py`

**Interfaces:**
- Consumes: the gap report's top entry.
- Produces: new unit tests raising that module's branch coverage; no production behavior change.

- [ ] **Step 1: Isolate the module's current misses**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ \
  --cov=file_mgmt.media --cov-report=term-missing -q | grep media.py
```
Read the `Missing` line/branch ranges. Open `htmlcov` for that file to see exactly which branches are unhit.

- [ ] **Step 2: Write a failing/branch-covering test for one uncovered path**

Target a real behavior, not a line count. Example — `_resize_image` downsizes only when the source exceeds `max_size`, else leaves it untouched:
```python
from PIL import Image
import io, os, tempfile


def _png(w, h):
    p = os.path.join(tempfile.mkdtemp(prefix="cov-"), "a.png")
    Image.new("RGB", (w, h), "red").save(p)
    return p


def test_resize_image_downsizes_when_larger_than_max():
    from file_mgmt.media import _resize_image  # adjust to the real signature from Step 1

    path = _png(2000, 1500)
    _resize_image(os.path.dirname(path), "a", "png", max_size=(800, 600))
    with Image.open(path) as im:
        assert im.width <= 800 and im.height <= 600


def test_resize_image_leaves_small_image_untouched():
    from file_mgmt.media import _resize_image

    path = _png(400, 300)
    _resize_image(os.path.dirname(path), "a", "png", max_size=(800, 600))
    with Image.open(path) as im:
        assert (im.width, im.height) == (400, 300)
```
(Read `file_mgmt/media.py` first and match the ACTUAL function name/signature/paths — the names above are illustrative.)

- [ ] **Step 3: Run just these tests, confirm pass + the target branches now covered**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/file_mgmt/test_media.py --cov=file_mgmt.media --cov-report=term-missing -q
```
Expected: PASS and the previously-missing branch ranges shrink.

- [ ] **Step 4: Repeat Steps 2-3 for each remaining high-value uncovered branch in the module** (rotate/EXIF orientation, thumbnail crop vs no-crop, the bare-except error path, etc.). Stop when the remaining misses are genuinely unreachable or not worth pinning (note them).

- [ ] **Step 5: Commit** the new test file. Message: `test(file_mgmt): cover media resize/rotate/thumbnail branches`.

- [ ] **Step 6: Loop** — re-run Task 2's script, take the new #1, repeat Task 3. Suggested initial passes: the top controller/runtime module, `common/api_commands.py`, one notify sender, and the display object with the most missed branches (these are the highest risk×gap).

---

### Task 4: Per-tier coverage + redundancy map

**Files:**
- Create: `docs/coverage/tier-map-2026-07-20.md`

**Interfaces:**
- Consumes: the ability to run each tier alone with `--cov`.
- Produces: which packages each tier (unit / web / characterization / ui) actually exercises, exposing both gaps (packages no tier covers) and redundancy (tiers covering the same lines — ui is 44 files and slow).

- [ ] **Step 1: Measure each tier's coverage separately**

```bash
for tier in unit web characterization ui; do
  QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/$tier \
    --cov --cov-report=json:cov-$tier.json -q || true
done
```
(ui/web need chromium; if it skips, record that the tier contributed nothing and re-run where chromium is available.)

- [ ] **Step 2: Build the tier map**

For each package, list which tiers cover it and the per-tier line%. Call out: (a) packages NO tier covers (pure gaps), (b) lines covered ONLY by the slow ui tier that a fast unit/web test could pin instead (candidate to move down the pyramid).

- [ ] **Step 3: Commit** the tier map. Message: "test: document per-tier coverage and redundancy".

---

### Task 5: Add a non-blocking coverage gate on changed code

**Files:**
- Modify: `pyproject.toml` (a `[tool.coverage.report]` advisory `fail_under` is NOT used for legacy; instead gate the diff)
- Create: `docs/coverage/README.md` gate section (extend Task 1's doc)
- Create: `docs/superpowers/plans/2026-07-19-refactor-verification-gates.md`-style note is NOT recreated; document the command instead.

**Interfaces:**
- Consumes: coverage.json + git diff.
- Produces: a pre-merge command that fails only when NEW/changed lines are under-covered, leaving the legacy baseline untouched.

- [ ] **Step 1: Add `diff-cover` to dev deps**

```toml
    "diff-cover>=9.0.0",
```
`uv sync`.

- [ ] **Step 2: Produce an XML report and run diff-cover against the base branch**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ --cov --cov-report=xml -q
uv run diff-cover coverage.xml --compare-branch=massive-reworks-and-new-ui --fail-under=80
```
Expected: passes when the branch's changed lines are ≥80% covered; fails listing the specific uncovered new lines. This gates NEW code only — legacy gaps don't block.

- [ ] **Step 3: Document the gate** in `docs/coverage/README.md`: run it before every merge; the global number is a trend (watch it in `htmlcov`), the diff number is the gate.

- [ ] **Step 4: (Optional) wire it into the existing verification-gates flow** if the repo adopts one, as a `before-merge` step alongside `ruff check`.

- [ ] **Step 5: Commit** (`pyproject.toml`, `uv.lock`, doc). Message: "test: gate coverage on changed lines via diff-cover".

---

## Verification

- Task 1: `coverage.json` exists; the validation step (Task 1 Step 4) shows a blueprint route file at nonzero coverage (proves thread capture). Full suite still green.
- Task 2: `docs/coverage/gap-report-*.md` exists and its top entries match intuition (control/safety/web code, not trivial helpers).
- Task 3: each iteration lowers the target module's missing-branch count; new tests assert behavior (open the image, check dims / read back state), not just execute lines; `git status` clean of stray temp artifacts.
- Task 4: tier map identifies at least the packages no tier covers.
- Task 5: `diff-cover` fails on a deliberately-under-tested throwaway change and passes once covered (verify once, then discard the throwaway).

End-to-end: re-run the Task 1 baseline command; the reported branch% is higher than the recorded baseline and the diff-cover gate is green on the branch.

## Self-Review

- **Spec coverage:** measurement (T1), analysis (T2), improvement loop (T3), tier/redundancy map (T4), regression gate (T5) — the full "figure out actual coverage + improve it" ask is covered.
- **Placeholder scan:** the only deliberately-deferred specifics are the *module targets* in T3, which are discovery outputs of T2 by design; the method, commands, and a complete worked example are concrete. The `_resize_image` code is explicitly marked illustrative with an instruction to match the real signature.
- **Type/name consistency:** `coverage.json`/`coverage.xml` artifact names, the `concurrency=["thread"]` requirement, and the interpreter incantation are used identically across tasks.
- **Risk:** the one real trap (web-layer code reading 0% without `concurrency=["thread"]`) is called out with an explicit validation gate in T1 before any analysis is trusted.
