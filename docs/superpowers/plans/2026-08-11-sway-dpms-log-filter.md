# Sway DPMS Log Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the two harmless monitor-power-down Sway/wlroots messages from `display.err.log` without hiding other compositor diagnostics or changing display behavior.

**Architecture:** Keep direct `execvp` for non-Sway display backends. For QtQuick backends, retain `display_launch.py` as a small parent process, stream Sway stderr through an exact line predicate, and return Sway's exit status to Supervisor.

**Tech Stack:** Python 3.14 standard library (`re`, `subprocess`, text streams), pytest.

## Global Constraints

- Suppress only the two approved message forms for any connector name.
- Require the expected Sway/wlroots subsystem and source-file context before suppressing a line.
- Preserve all unmatched stderr bytes as UTF-8 text with replacement decoding and immediate flushing.
- Preserve the existing non-Sway `execvp` behavior, Sway kiosk configuration, environment variables, process group, and child exit status.
- Add no dependency and make no DRM, DPMS, rendering, or Supervisor configuration change.

---

### Task 1: Exact Sway stderr predicate and relay

**Files:**
- Modify: `display_launch.py:17-28` and after `_ensure_runtime_dir`
- Test: `tests/ui/test_display_launch.py`

**Interfaces:**
- Consumes: iterable text lines from Sway's captured stderr and a writable text destination.
- Produces: `_is_ignored_sway_stderr(line: str) -> bool` and `_relay_sway_stderr(source: Iterable[str], destination: TextIO) -> None`.

- [ ] **Step 1: Write failing predicate tests**

Add parameterized tests that include both connector families and varying source line numbers:

```python
import io

import pytest


@pytest.mark.parametrize(
    "line",
    [
        "00:00:55.148 [ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: Atomic commit failed: Device or resource busy\n",
        "00:00:55.148 [ERROR] [wlr] [backend/drm/atomic.c:912] connector HDMI-A-1: Atomic commit failed: Device or resource busy\n",
        "00:00:55.148 [ERROR] [sway/desktop/output.c:300] Page-flip failed on output DP-1\n",
        "00:00:55.148 [ERROR] [sway/desktop/output.c:411] Page-flip failed on output HDMI-A-1\n",
    ],
)
def test_known_powered_down_output_errors_are_ignored(line):
    assert display_launch._is_ignored_sway_stderr(line)
```

- [ ] **Step 2: Write failing preservation tests**

Cover close but actionable diagnostics and the actual relay contract:

```python
@pytest.mark.parametrize(
    "line",
    [
        "[ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: Atomic commit failed: Invalid argument\n",
        "[ERROR] [wlr] connector DP-1: Atomic commit failed: Device or resource busy\n",
        "[ERROR] [sway/desktop/output.c:300] Page-flip failed while enabling output DP-1\n",
        "Qt warning: Page-flip failed on output DP-1\n",
    ],
)
def test_other_display_errors_are_preserved(line):
    assert not display_launch._is_ignored_sway_stderr(line)


def test_relay_removes_only_known_noise():
    source = io.StringIO(
        "[ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: Atomic commit failed: Device or resource busy\n"
        "actionable display error\n"
        "[ERROR] [sway/desktop/output.c:300] Page-flip failed on output DP-1\n"
    )
    destination = io.StringIO()

    display_launch._relay_sway_stderr(source, destination)

    assert destination.getvalue() == "actionable display error\n"
```

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/ui/test_display_launch.py -q
```

Expected: failures reporting missing `_is_ignored_sway_stderr` and `_relay_sway_stderr`.

- [ ] **Step 4: Implement the exact predicate and relay**

Add two compiled patterns whose anchors allow only timestamp/logger prefixes and connector tokens, then relay unmatched lines unchanged:

```python
_SWAY_IGNORED_STDERR_PATTERNS = (
    re.compile(
        r"\[wlr\] \[backend/drm/atomic\.c:\d+\] connector \S+: "
        r"Atomic commit failed: Device or resource busy$"
    ),
    re.compile(r"\[sway/desktop/output\.c:\d+\] Page-flip failed on output \S+$"),
)


def _is_ignored_sway_stderr(line: str) -> bool:
    message = line.rstrip("\r\n")
    return any(pattern.search(message) for pattern in _SWAY_IGNORED_STDERR_PATTERNS)


def _relay_sway_stderr(source: Iterable[str], destination: TextIO) -> None:
    for line in source:
        if _is_ignored_sway_stderr(line):
            continue
        destination.write(line)
        destination.flush()
```

Import `re`, `Iterable` from `collections.abc`, and `TextIO` from `typing`; keep the functions independent from Qt/display modules.

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/ui/test_display_launch.py -q
```

Expected: all launcher tests pass.

- [ ] **Step 6: Commit the predicate and relay**

```bash
jj desc -m "Filter known Sway DPMS log noise"
jj new
```

---

### Task 2: Sway child lifecycle integration

**Files:**
- Modify: `display_launch.py:64-86`
- Test: `tests/ui/test_display_launch.py`

**Interfaces:**
- Consumes: Sway `argv: list[str]`, inherited environment, `_relay_sway_stderr` from Task 1.
- Produces: `_run_sway(argv: list[str]) -> int`; `main()` exits with that status for Sway and still uses `os.execvp` for every other backend.

- [ ] **Step 1: Write a failing child lifecycle test**

Use a real Python subprocess so the test exercises decoding, stderr filtering, EOF draining, and exit-status propagation without display hardware:

```python
def test_run_sway_filters_stderr_and_returns_child_status(capsys):
    script = (
        "import sys; "
        "sys.stderr.write('[ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: "
        "Atomic commit failed: Device or resource busy\\n'); "
        "sys.stderr.write('actionable display error\\n'); "
        "sys.stderr.write('[ERROR] [sway/desktop/output.c:300] "
        "Page-flip failed on output DP-1\\n'); "
        "raise SystemExit(7)"
    )

    returncode = display_launch._run_sway([sys.executable, "-c", script])

    assert returncode == 7
    assert capsys.readouterr().err == "actionable display error\n"
```

- [ ] **Step 2: Run the lifecycle test and verify it fails**

Run:

```bash
uv run pytest tests/ui/test_display_launch.py::test_run_sway_filters_stderr_and_returns_child_status -q
```

Expected: failure reporting missing `_run_sway`.

- [ ] **Step 3: Implement the Sway runner**

Add the subprocess runner with stdout inherited and only stderr captured:

```python
def _run_sway(argv: list[str]) -> int:
    with subprocess.Popen(
        argv,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        if process.stderr is None:
            raise RuntimeError("Sway stderr pipe was not created")
        _relay_sway_stderr(process.stderr, sys.stderr)
        return process.wait()
```

Import `subprocess`. Do not create a new session or process group; Sway and its display child must remain in Supervisor's existing group.

- [ ] **Step 4: Route only Sway through the runner**

Replace the unconditional `os.execvp` at the end of `main()` with:

```python
try:
    if argv[0] == "sway":
        sys.exit(_run_sway(argv))
    os.execvp(argv[0], argv)
except OSError:
    log.exception("Failed to exec: %s", " ".join(argv))
    sys.exit(1)
```

The existing backend selection makes `argv[0] == "sway"` true only for `qtquick_*` displays.

- [ ] **Step 5: Run focused launcher verification**

Run:

```bash
uv run pytest tests/ui/test_display_launch.py -q
uv run ruff check display_launch.py tests/ui/test_display_launch.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 6: Run the subprocess smoke scenario**

Run the focused lifecycle test without pytest output capture:

```bash
uv run pytest tests/ui/test_display_launch.py::test_run_sway_filters_stderr_and_returns_child_status -q -s
```

Expected: one test passes; the assertion proves the two known messages were removed, the actionable line survived unchanged, and exit status `7` was returned.

- [ ] **Step 7: Commit lifecycle integration**

```bash
jj desc -m "Relay filtered Sway display logs"
jj new
```
