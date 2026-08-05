# Deriving pid_sp's gains from the identified model: measured, and rejected

PB, Ti and Td are numbers a user is expected to reach by trial, and none of them
is a property of the grill alone. Once `fopdt_identifier` names the chamber, the
textbook move is to stop asking: SIMC turns an identified integrating process
into gains directly, collapsing three knobs into one closed-loop speed dial.

It was implemented, swept, and reverted. It is worse than the shipped constants
at every setpoint, and badly worse at 450 F.

## What was run

`docs/superpowers/experiments/pid_cook_chain.py`, MAKGrillSim, 3 seeds, two
successive cooks, the second restoring the model the first learned. Gains were
re-derived wherever a model arrived -- restored at Hold entry, or promoted
mid-cook.

For the integrating form, `K_i e^-(theta s) / s`:

    Kc = 1 / (K_i (tau_c + theta))      PB = 1/Kc by this controller's definition
    Ti = 4 (tau_c + theta)              tau_c = lambda * theta

`band` arms derive PB only and leave the configured Ti alone, to separate the
proportional change from the integral one.

## Cook 2, median over seeds

| arm              | 225 over / in5 | 350 over / in5 | 450 over / in5  |
| ---------------- | -------------- | -------------- | --------------- |
| off (control)    | 7.6 / 94.6     | 11.5 / 91.5    | 13.1 / 88.7     |
| lambda 1.0, band | 15.2 / 93.3    | 15.4 / 87.1    | 41.6 / 50.3     |
| lambda 0.5, band | 16.7 / 93.5    | 16.6 / 89.9    | 45.3 / 59.5     |
| lambda 1.0, full | 14.1 / 90.1    | 17.3 / 86.1    | 45.1 / 49.4     |
| lambda 2.0, full | 14.3 / 86.4    | 17.5 / 73.1    | 46.9 / 46.2     |

Settle time at 450 goes from 1526 s to roughly 11 000 s: the loop stops settling
within the run at all.

The control matters here. An earlier pass ran an arm labelled `shipped` that
passed an empty config -- which did not disable the derivation, because it was
on by default. That arm reproduced the tuned numbers exactly and would have been
reported as the baseline. The control above is an explicit off switch.

## Why it loses

The gains were never the binding constraint. The operating point is.

`self.p = self.kp * error + self.center`, and `center` is a heuristic fixed at
0.5. At 450 F the identified duty that actually holds the chamber, `-c0/K_i`, is
**0.205**. So at zero error the proportional term asks for 0.3 duty more than the
grill needs, and the integral is the only term that can take it back.

Widening PB from 60 to `K_i (tau_c + theta)` = 99 divides `kp` by 1.66, and
`ki = kp/ti` falls with it. The correction that was already the bottleneck gets
about 40% slower, while the bias it is correcting is unchanged. Every arm trades
a term that was helping for one that was not the problem.

This is not a defect in SIMC. SIMC assumes a PI acting on a process whose bias is
correct; this controller carries a deliberate, known-wrong bias and relies on
integral action to erase it. A tuning rule derived for the first structure does
not describe the second.

## What this says to do instead

Fix the operating point before touching the gains. `hold_duty()` already names it
and is already used, but only as a one-shot integral seed once the error is
inside the stable window -- which is the end of the approach, after the overshoot
has happened. The whole climb still runs against 0.5.

Substituting it into `center` was measured earlier in the session and lost
(225 F: 90.8% -> 88.5% in band). That measurement predates the cook boundary
carrying a model at all, so on cook 1 the substitution could only ever arrive
late. It deserves re-running now that cook 2 starts with the chamber identified.

Caveat for whoever does: `-c0/K_i` is the duty that holds the operating point the
model was IDENTIFIED at, because c0 absorbs the loss at that temperature. Using
it at a different setpoint is an extrapolation the model does not support.

## Outcome

Done, and it won where the gains lost: cook 2 overshoot fell to 2.6/2.7/2.7 F at
225/350/450 against 7.6/11.5/3.2 without it. The caveat above was real and had to
be paid -- scaling the operating point with the chamber's rise above ambient --
and the same scaling turned out to be needed on the model itself, which is what
`retarget` does. See the commit that follows this one.
