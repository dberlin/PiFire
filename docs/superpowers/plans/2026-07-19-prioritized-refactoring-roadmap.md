# Prioritized Refactoring Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the refactoring scan into an ordered, PR-sized roadmap that reduces complexity while preserving PiFire behavior.

**Architecture:** Treat each independent subsystem as its own branch/PR with characterization tests first. Prefer thin route/controller orchestration, service/repository boundaries for business logic, shared base classes/helpers for duplicated hardware/display code, and behavior-preserving extractions before any behavior changes.

**Tech Stack:** Python 3.14, Flask, Flask-SocketIO, pytest, existing characterization/golden tests, uv, Ruff once added to dev dependencies.

## Global Constraints

- Behavior-preserving unless a task explicitly calls out an intentional behavior change.
- One branch per priority item; do not combine unrelated subsystems in a single PR.
- Add or extend characterization tests before extracting code from large functions.
- Keep existing public module names and import compatibility unless the detailed phase plan says otherwise.
- Run focused tests after each task and the broad suite before merge.
- Existing plans in `docs/superpowers/plans/` remain source-of-truth for phases already planned.

---

## Priority Overview

| Priority | Workstream | Why now | Primary files | Risk | Status |
|---|---|---|---|---|---|
| P0 | Tooling and execution gates | Makes every later refactor safer | `pyproject.toml`, `ruff.toml`, CI/test docs | Low | New |
| P1 | Remaining large Flask routes | Highest complexity in web layer after Phase D | `blueprints/cookfile/routes.py`, `blueprints/recipes/routes.py`, `blueprints/wizard/routes.py`, `blueprints/api/routes.py`, `blueprints/tuner/routes.py`, `blueprints/pellets/routes.py`, `blueprints/history/routes.py`, `blueprints/update/routes.py` | Medium | New |
| P2 | Split controller runtime loop | High runtime risk and high maintainability gain | `controller/runtime/modes/base.py` | High | Existing plan F |
| P3 | Flex display object decomposition | Largest production Python file and many long draw methods | `display/flexobject.py`, `display/base_flex.py` | High | New |
| P4 | Hardware/probe/display duplication cleanup | Removes duplicated platform/probe/driver code | `grillplat/*`, `probes/ads*.py`, `probes/virtual_*.py`, `display/st7789*.py` | Medium | Partially existing plans C/G/E |
| P5 | Small contained logic tables | Low-risk wins after bigger seams exist | `notify/notifications.py`, `controller/pid_*.py`, `common/defaults.py` | Low-Medium | Existing H/I + new defaults |
| P6 | App factory and persistence boundary | Useful after routes/services stabilize | `app.py`, `file_mgmt/*`, `common/datastore.py` | Medium | New |
| P7 | Error handling/logging cleanup | Broad sweep best done last or opportunistically | many files | Medium | New |

---

### Task 1: P0 — Add Refactoring Gates and Lint Baseline

**Files:**
- Modify: `pyproject.toml`
- Modify: `ruff.toml`
- Create: `docs/superpowers/plans/2026-07-19-refactor-verification-gates.md`

**Interfaces:**
- Consumes: existing pytest suite and `uv` project configuration.
- Produces: repeatable commands used by every later refactor PR.

- [ ] **Step 1: Create an isolated branch**

```bash
git checkout -b refactor/gates-and-lint
```

- [ ] **Step 2: Add Ruff to dev dependencies**

Add this entry to the `[dependency-groups].dev` list in `pyproject.toml`:

```toml
    "ruff>=0.8.0",
```

- [ ] **Step 3: Keep Ruff initially non-invasive**

Keep `ruff.toml` focused on line length first:

```toml
# Line length is relaxed: ~99% of existing lines fit within 120 columns.
line-length = 120
```

Do not enable broad rule sets until after the major behavior-preserving refactors land.

- [ ] **Step 4: Write verification gate doc**

Create `docs/superpowers/plans/2026-07-19-refactor-verification-gates.md` with these commands:

```markdown
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
```

- [ ] **Step 5: Verify**

Run:

```bash
uv sync
uv run ruff check .
uv run python -m compileall app.py blueprints common controller display file_mgmt grillplat notify probes
```

Expected: commands run successfully or produce a documented initial lint baseline.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml ruff.toml docs/superpowers/plans/2026-07-19-refactor-verification-gates.md
git commit -m "chore: add refactor verification gates"
```

---

### Task 2: P1 — Plan and Execute Remaining Flask Route Service Extractions

**Files:**
- Create: `docs/superpowers/plans/YYYY-MM-DD-cookfile-recipes-route-services.md`
- Modify: `blueprints/cookfile/routes.py`
- Modify: `blueprints/recipes/routes.py`
- Modify later PRs: `blueprints/wizard/routes.py`, `blueprints/api/routes.py`, `blueprints/tuner/routes.py`, `blueprints/pellets/routes.py`, `blueprints/history/routes.py`, `blueprints/update/routes.py`
- Likely create: `blueprints/cookfile/services.py`, `blueprints/recipes/services.py`
- Test: `tests/web/`, `tests/characterization/`

**Interfaces:**
- Consumes: existing Phase D service-layer pattern from `docs/superpowers/plans/2026-07-16-phaseD-blueprints-service.md`.
- Produces: route handlers that parse request/action and delegate to tested service functions.

- [ ] **Step 1: Start with cookfile + recipes only**

```bash
git checkout -b refactor/cookfile-recipes-services
```

These are the largest remaining route handlers:

```text
blueprints/cookfile/routes.py:24 cookfile_page      415 lines
blueprints/recipes/routes.py:28 recipes_data       347 lines
```

- [ ] **Step 2: Write a detailed implementation plan before editing code**

Use `superpowers:writing-plans` and create:

```text
docs/superpowers/plans/YYYY-MM-DD-cookfile-recipes-route-services.md
```

Required decomposition:

```text
blueprints/cookfile/routes.py       route-only request/response orchestration
blueprints/cookfile/services.py     cookfile action handlers and render-data builders
blueprints/recipes/routes.py        route-only request/response orchestration
blueprints/recipes/services.py      recipe action handlers and upload/list/edit helpers
```

- [ ] **Step 3: Add characterization tests before extraction**

Add tests for the current POST/GET actions handled by `cookfile_page`, `cookfile_update`, and `recipes_data`.

Minimum required test names:

```python
def test_cookfile_page_get_renders_index(client): ...
def test_cookfile_page_json_dl_cookfile_preserves_response_shape(client): ...
def test_cookfile_update_json_selected_unselected_preserves_response_shape(client): ...
def test_recipes_data_get_preserves_response_shape(client): ...
def test_recipes_data_upload_rejects_disallowed_file(client): ...
```

- [ ] **Step 4: Extract action dispatch maps**

Use explicit dispatch dictionaries instead of long `if request.form[...]` / JSON action chains:

```python
COOKFILE_JSON_ACTIONS = {
    "dl_cookfile": handle_download_cookfile,
    "dl_eventfile": handle_download_eventfile,
    "dl_graphfile": handle_download_graphfile,
}

COOKFILE_FORM_ACTIONS = {
    "ulcookfile": handle_upload_cookfile,
    "ulcookfilereq": handle_upload_cookfile_request,
}
```

- [ ] **Step 5: Verify focused web behavior**

Run:

```bash
uv run pytest tests/web tests/characterization -q
```

Expected: all tests pass with no response-shape changes.

- [ ] **Step 6: Repeat as separate PRs for wizard/api/tuner/pellets/history/update**

Order for follow-up route PRs:

1. `blueprints/api/routes.py` — API response shape is easiest to characterize.
2. `blueprints/pellets/routes.py` — medium-size single domain.
3. `blueprints/history/routes.py` + shared cookfile/history event-summary helper.
4. `blueprints/tuner/routes.py` — controller-adjacent, keep isolated.
5. `blueprints/wizard/routes.py` + `blueprints/update/routes.py` — higher operational risk.

---

### Task 3: P2 — Execute Existing ControlMode Run Split Plan

**Files:**
- Existing detailed plan: `docs/superpowers/plans/2026-07-18-phaseF-controlmode-run-split.md`
- Modify: `controller/runtime/modes/base.py`
- Test: existing 49 behavioral characterization tests referenced by the phase plan

**Interfaces:**
- Consumes: `ControlMode.run()` current behavior.
- Produces: smaller tick-phase methods without changing runtime behavior.

- [ ] **Step 1: Execute the existing detailed plan, not this roadmap**

```bash
git checkout -b refactor/controlmode-run-split
```

Open and follow:

```text
docs/superpowers/plans/2026-07-18-phaseF-controlmode-run-split.md
```

- [ ] **Step 2: Preserve these already-extracted seams**

Do not regress these helper boundaries:

```text
ControlMode._process_control_flags(...)
ControlMode._build_status_data(...)
ControlMode._smoke_plus_fan_tick(...)
```

- [ ] **Step 3: Verify**

Run the focused commands from the phase plan, then:

```bash
uv run pytest tests/unit tests/characterization -q
```

Expected: behavior characterization tests pass.

---

### Task 4: P3 — Split Flex Display Widgets and Drawing Helpers

**Files:**
- Create: `docs/superpowers/plans/YYYY-MM-DD-flexobject-widget-split.md`
- Modify: `display/flexobject.py`
- Likely create: `display/flex/widgets/base.py`, `display/flex/widgets/gauges.py`, `display/flex/widgets/cards.py`, `display/flex/widgets/inputs.py`, `display/flex/widgets/status.py`, `display/flex/drawing.py`
- Test: display unit/characterization tests

**Interfaces:**
- Consumes: `FlexObject_TypeMap`, existing widget class names, and `DisplayBase` imports.
- Produces: same public widget classes and type map with smaller files and shared drawing helpers.

- [ ] **Step 1: Write a detailed widget-split plan before editing code**

Required public compatibility:

```python
from display.flexobject import FlexObject, GaugeCircle, GaugeCompact, ProbeCard, GaugeEmber
```

must continue to work.

- [ ] **Step 2: Add image/golden characterization before extraction**

At minimum, capture representative render outputs for:

```text
GaugeCircle
GaugeCompact
ProbeCard
GaugeEmber
SystemCard
InputNumber
InputNumberSimple
HeaderBar
ButtonRow
```

- [ ] **Step 3: Extract drawing helpers first**

Target helpers:

```python
def rounded_card(draw, bounds, *, fill, outline=None, radius=12): ...
def centered_text(draw, bounds, text, font, *, fill): ...
def draw_arc_gauge(draw, bounds, *, value, minimum, maximum, color): ...
def resolve_widget_accent(name: str, fallback: str) -> str: ...
```

- [ ] **Step 4: Move classes by family while keeping re-export shim**

`display/flexobject.py` should become a compatibility module that imports/re-exports the moved classes and keeps `FlexObject_TypeMap` stable.

- [ ] **Step 5: Verify display behavior**

Run:

```bash
uv run pytest tests/unit/display tests/characterization -q
```

Expected: all display snapshots/golden tests pass.

---

### Task 5: P4 — Consolidate Hardware, Probe, and Driver Duplication

**Files:**
- Existing detailed plans: `2026-07-16-phaseC-display-driver-matrix.md`, `2026-07-18-phaseG-grillplat-mixin.md`, `2026-07-18-phaseE-meater-dedup.md`
- New probe plan: `docs/superpowers/plans/YYYY-MM-DD-ads-virtual-probe-dedup.md`
- Modify likely: `probes/ads1015_adafruit.py`, `probes/ads1115.py`, `probes/ads1115_adafruit.py`, `probes/virtual_lowest.py`, `probes/virtual_average.py`, `probes/virtual_median.py`, `probes/virtual_highest.py`

**Interfaces:**
- Consumes: existing probe module import names from settings/manifests.
- Produces: shared probe base/config classes while preserving module-level compatibility.

- [ ] **Step 1: Land existing grillplat plan first if not already merged**

Follow:

```text
docs/superpowers/plans/2026-07-18-phaseG-grillplat-mixin.md
```

- [ ] **Step 2: Write focused ADS/virtual probe dedup plan**

Required shape:

```python
@dataclass(frozen=True)
class ADSProbeConfig:
    chip: str
    address: int | None
    gain: int | None

class ADSProbeBase(ReadProbes): ...
```

Virtual probes should share one aggregation base with an injected reducer:

```python
class VirtualAggregateProbe(ReadProbes):
    def __init__(self, reducer: Callable[[list[float]], float], label: str): ...
```

- [ ] **Step 3: Keep shim modules**

Existing module names must continue to import:

```text
probes/virtual_lowest.py
probes/virtual_average.py
probes/virtual_median.py
probes/virtual_highest.py
probes/ads1015_adafruit.py
probes/ads1115.py
probes/ads1115_adafruit.py
```

- [ ] **Step 4: Verify hardware-free tests**

Run:

```bash
uv run pytest tests/unit/probes tests/characterization -q
```

Expected: existing probe behavior passes without real hardware.

---

### Task 6: P5 — Land Contained Logic-Table Refactors

**Files:**
- Existing plans: `2026-07-18-phaseH-notify-event-table.md`, `2026-07-18-phaseI-pid-base.md`
- New defaults plan: `docs/superpowers/plans/YYYY-MM-DD-default-settings-schema.md`
- Modify: `notify/notifications.py`, `controller/pid_*.py`, `common/defaults.py`

**Interfaces:**
- Consumes: existing notification keys, PID controller API, and settings JSON shape.
- Produces: table-driven notification and settings construction with stable output.

- [ ] **Step 1: Execute notification event-table plan**

Follow:

```text
docs/superpowers/plans/2026-07-18-phaseH-notify-event-table.md
```

- [ ] **Step 2: Execute PID base plan**

Follow:

```text
docs/superpowers/plans/2026-07-18-phaseI-pid-base.md
```

- [ ] **Step 3: Write default-settings schema plan**

The plan must characterize exact output of:

```python
from common.defaults import default_settings, default_notify_services
```

before changing implementation.

- [ ] **Step 4: Split defaults into named builders without changing JSON shape**

Required target helpers:

```python
def default_platform_settings() -> dict: ...
def default_display_settings() -> dict: ...
def default_control_settings() -> dict: ...
def default_safety_settings() -> dict: ...
def default_probe_settings() -> dict: ...
```

- [ ] **Step 5: Verify exact defaults**

Run:

```bash
uv run pytest tests/unit/common tests/characterization -q
```

Expected: default settings snapshots are byte-for-byte equivalent except intentional UUID/date fields masked by tests.

---

### Task 7: P6 — Introduce App Factory and Repository Boundaries

**Files:**
- Create: `docs/superpowers/plans/YYYY-MM-DD-app-factory-repositories.md`
- Modify: `app.py`
- Likely create: `common/app_factory.py`, `file_mgmt/repositories.py` or domain-specific repositories
- Modify after route services: `blueprints/*/routes.py`, `blueprints/*/services.py`

**Interfaces:**
- Consumes: stabilized service-layer route code from P1.
- Produces: `create_app(config=None)` and storage/repository classes that make tests less import-order dependent.

- [ ] **Step 1: Wait until P1 route services are merged**

Do not start app factory before large route handlers are thinned; otherwise route import side effects will obscure factory boundaries.

- [ ] **Step 2: Write app-factory plan**

Required target API:

```python
def create_app(config: dict | None = None) -> Flask: ...
def register_blueprints(app: Flask) -> None: ...
def create_socketio(app: Flask) -> SocketIO: ...
```

- [ ] **Step 3: Preserve import compatibility**

`app.py` should still expose:

```python
app = create_app()
socketio = create_socketio(app)
```

so Gunicorn/current launch paths keep working.

- [ ] **Step 4: Add repository seams for cookfile/recipe storage**

Target interfaces:

```python
class CookfileRepository:
    def read(self, filename: str) -> dict: ...
    def write(self, filename: str, data: dict) -> None: ...
    def list(self) -> list[str]: ...

class RecipeRepository:
    def read(self, filename: str) -> dict: ...
    def write(self, filename: str, data: dict) -> None: ...
    def list(self) -> list[str]: ...
```

- [ ] **Step 5: Verify web startup and tests**

Run:

```bash
uv run pytest tests/web tests/unit -q
```

Expected: app test clients still initialize without relying on global import side effects.

---

### Task 8: P7 — Normalize Error Handling and Logging Opportunistically

**Files:**
- Create: `docs/superpowers/plans/YYYY-MM-DD-error-logging-normalization.md`
- Modify opportunistically: files touched by P1-P6
- Avoid broad whole-repo sweeps unless tests are strong for the touched subsystem

**Interfaces:**
- Consumes: existing logger conventions from `common.common.create_logger` and module-level loggers.
- Produces: fewer bare exceptions, fewer `print(...)` calls, and safer subprocess usage.

- [ ] **Step 1: Write policy before editing**

Policy:

```text
1. Replace bare except with specific exception types where the failure mode is known.
2. If broad Exception is required at a hardware boundary, log device name/action and re-raise or return the existing fallback.
3. Replace print with logger calls in production modules.
4. Replace shell=True subprocess calls with argument-list subprocess calls unless shell syntax is required.
```

- [ ] **Step 2: Only clean files already under active refactor**

For each touched file, run this scan:

```bash
python - <<'PY'
from pathlib import Path
for p in [Path('PATH/TO/TOUCHED_FILE.py')]:
    text = p.read_text()
    for token in ['except:', 'except Exception', 'print(', 'shell=True']:
        if token in text:
            print(p, token)
PY
```

- [ ] **Step 3: Verify focused behavior**

Run the tests for the same subsystem being cleaned.

---

## Recommended Landing Order

1. **P0 gates** — enables consistent verification.
2. **P5 notification/PID existing plans** if you want low-risk momentum, or **P1 cookfile/recipes routes** if you want maximum complexity reduction first.
3. **P1 remaining route services** — one domain per PR.
4. **P2 ControlMode run split** — after current runtime branch divergence is low.
5. **P4 grillplat/probe dedup** — hardware abstractions after controller seams are cleaner.
6. **P3 flex display split** — high-value but needs snapshot discipline.
7. **P6 app factory/repositories** — best after route services exist.
8. **P7 error/logging cleanup** — continuous opportunistic cleanup, not one giant PR.

## Self-Review

**Spec coverage:** Covers all opportunities from the scan: routes, flex display, duplicated hardware/probes/display drivers, controller loop, defaults, app factory, persistence, linting, and error/logging.

**Placeholder scan:** No `TBD`, unconstrained `TODO`, or undefined future behavior. New independent subsystems are explicitly required to get their own detailed plans before code changes.

**Type consistency:** Target APIs are named consistently across tasks: `create_app`, `register_blueprints`, `create_socketio`, `CookfileRepository`, `RecipeRepository`, `ADSProbeConfig`, `ADSProbeBase`, and `VirtualAggregateProbe`.
