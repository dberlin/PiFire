# Sway DPMS Log Filter Design

## Problem

The QtQuick display launcher replaces itself with Sway. Supervisor therefore writes Sway's stderr directly to `logs/display.err.log`. While a monitor is powered down, wlroots/Sway can repeatedly emit these harmless lines even though display power control and the UI continue to work:

```text
[ERROR] [wlr] [backend/drm/atomic.c:81] connector <connector>: Atomic commit failed: Device or resource busy
[ERROR] [sway/desktop/output.c:300] Page-flip failed on output <connector>
```

The noise obscures actionable display failures. Sway exposes verbosity increases but no per-message suppression facility.

## Scope

Suppress only those two known message forms for any connector name. Preserve every other Sway, wlroots, Qt, and display-process diagnostic. The filter applies only when `display_launch.py` launches Sway for a `qtquick_*` display; non-Sway display backends retain their existing direct `execvp` path.

## Design

`display_launch.py` will run Sway as a child process with stderr captured as text. A small line predicate will recognize the exact stable message body after Sway's variable timestamp and logger prefix:

- wlroots atomic commit failure ending in `Atomic commit failed: Device or resource busy`
- Sway output page-flip failure containing `Page-flip failed on output`

Each recognized line is discarded. Every other line is written immediately to the launcher's inherited stderr without rewriting it. Matching will require the expected subsystem/source context as well as the message body, preventing unrelated application messages with similar wording from being hidden.

The launcher will wait for Sway and terminate with Sway's exit status. Supervisor's `stopasgroup=true` continues to terminate the launcher, Sway, and the display process as one process group. The kiosk configuration and `PIFIRE_DISPLAY_CMD` lifetime coupling remain unchanged.

## Error and Lifecycle Behavior

- Failure to start Sway remains an explicit launcher error and exits nonzero.
- Unexpected stderr decoding bytes are preserved through replacement decoding rather than terminating the relay.
- EOF on stderr is drained before the child exit status is returned.
- Nonzero Sway exits remain visible to Supervisor and trigger the existing restart policy.
- No DRM, DPMS, compositor, or rendering behavior is changed; this is a logging-boundary filter only.

## Verification

Focused launcher tests will pass representative timestamped lines through the predicate and prove:

1. both reported messages are suppressed for multiple connector names;
2. unrelated atomic commit failures are preserved;
3. other page-flip diagnostics are preserved;
4. normal Sway/wlroots diagnostics are preserved;
5. the stderr relay emits preserved lines unchanged; and
6. the launcher returns the Sway child's exit status.

The focused `tests/ui/test_display_launch.py` suite will be run after implementation. A subprocess smoke scenario will exercise the relay with a fake child emitting both noisy and actionable lines, confirming only the actionable line reaches stderr.
