# MPC Learning Status Relocation

## Problem

`MpcLearningPanel` currently renders as a sixth child of the fixed-height cook-control grid. The shared `.pf-btn` treatment makes it look like a primary cook-mode action, and at 1280×720 its third line can be clipped below the dashboard. Model learning is status and configuration, not a mode command.

## Decision

Move `MpcLearningPanel` ownership from `ControlButtons` to `Dashboard`. Render it in `pf-dash-rightcol` immediately after the optional `HopperGauge`.

When a hopper distance sensor is present, the learning status appears directly below Hopper. When no distance sensor is present, it remains in the right column after the system duty pills. MPC access must not depend on hopper hardware.

## Component and Data Flow

`Dashboard` already loads the selected controller, ambient temperature, live temperature unit, and safety maximum. It will pass those values directly to `MpcLearningPanel`.

`ControlButtons` will stop importing or rendering `MpcLearningPanel` and will no longer accept `selectedController` or `mpcAmbientC`. Its API returns to cook-mode commands only.

`MpcLearningPanel` keeps its existing MPC-only visibility check, report polling, calibration commands, and modal. Only the trigger's placement and dashboard-specific presentation change.

## Presentation

The trigger remains a full-width touch target but becomes a compact status row sized for the right column. It must not inherit the cook-control row's 82px grid track or appear as another mode button. The label remains `MPC learning: <status>` and continues to open the existing dialog.

The trigger gets a dedicated dashboard class layered on the existing button behavior. The right-column layout reserves a fixed touch-target height for it; Hopper continues to use the remaining flexible height. Tablet and phone layouts keep the trigger in document order after Hopper and preserve a minimum usable touch height.

## Error and Empty States

No new error handling is introduced. The existing trigger states—`loading`, the lower-cased evidence status, and `unavailable`—remain unchanged. If controller settings cannot prove that MPC is selected, the trigger remains hidden. If Hopper is absent, only Hopper is omitted.

## Verification

Automated coverage will assert:

- the trigger is inside `rightCol` and absent from the cook-control grid;
- the trigger follows Hopper when a distance sensor is present;
- the trigger remains visible in `rightCol` without a distance sensor;
- clicking it still opens `MPC model learning`;
- desktop 1280×720 and panel 800×480 layouts keep the trigger reachable and on-screen.

Browser verification will render fake MPC settings and evidence data at both dashboard sizes, inspect placement, open the modal, and capture the resulting layout.