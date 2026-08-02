# MPC Recalibration Runbook

Follow this in order. Steps 1–2 must land before any calibration data is worth capturing.

## 1. Deploy the fan-authority fix

Deploy a build containing `controller_fan_authority` (Task 1 of
`docs/superpowers/plans/2026-08-02-mpc-fan-authority-and-calibration.md`).

## 2. Give the controller the fan

Settings > PWM Fan > **PWM Control: on**. Save. Settings > Controller with MPC
selected must now save without the fan-authority error.

## 3. Confirm the fan actually modulates

Start a Hold cook and watch the control log:

```
grep set_duty_cycle logs/control.log | tail -20
```

Expect duty changes tracking the firing rate. **If this is empty, stop** — the
calibration below would capture a grill running at one fixed airflow, which is
what invalidated the previous attempt.

## 4. Discard the contaminated log

Any `controller/mpc_calibration_log.csv` captured before step 2 describes the
grill with the fan pinned at 100 %. Move it aside; do not fit it.

## 5. Capture a fresh log

Settings > Controller > **Log Calibration Data: on**. Run a cook that includes a
full heat-up to a high setpoint and at least one step change down — the fit
needs both to separate the steady gain from the deadtime. 60–90 minutes is
enough.

## 6. Fit

```bash
uv run python -m controller.update_mpc controller/mpc_calibration_log.csv
```

Read the reported RMSE first. Above 10 °C the fit does not describe the log —
re-capture with more excitation rather than accepting the numbers. Heed the
horizon warning if it appears.

## 7. Apply

Paste the emitted JSON into Settings > Controller, field by field. Turn **Log
Calibration Data off**. Save.

## 8. Verify

Run a Hold cook at 450 °F. Expect the firing rate to begin rolling off several
minutes before the setpoint rather than at it. Record peak temperature; the
pre-fix baseline for this grill was **+70 °F** (520 °F peak at a 450 °F
setpoint), reached 263 s after braking began.
