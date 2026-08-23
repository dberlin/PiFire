# MCP9601 Support and Thermocouple Health Detection — Design

**Date:** 2026-08-23  
**Status:** Approved in chat; awaiting written-spec review

## Problem

PiFire supports the MCP9600 and MAX31856 thermocouple converters, but not the
MCP9601. The MCP9601 adds hardware open-circuit and short-to-supply detection
when the surrounding board includes the required VSENSE network. The selected
Playing With Fusion `SEN-30010-W` board includes that circuitry; chip identity
alone does not prove that another board does.

A second failure mode applies to thermocouple converters without usable hardware
detection. A disconnected or electrically collapsed thermocouple can produce
zero thermocouple EMF, causing the reported hot-junction temperature to follow
the converter's cold-junction/ambient temperature. The reading is numeric and
plausible, so PiFire's current numeric temperature guards cannot distinguish it
from a connected probe near ambient.

A confirmed primary-pit-probe fault cannot safely drive combustion control. It
must transition the controller to Error before the next physical actuation. A
food or auxiliary fault does not drive combustion; that probe becomes
unavailable and notifies while the cook continues.

## Evidence and existing boundaries

- `probes/mcp9600_adafruit.py` reads only the Adafruit sensor's hot-junction
  `temperature`; the same library also exposes `ambient_temperature` and
  `delta_temperature`.
- `probes/max31856_adafruit.py` reads only hot-junction `temperature`; the
  Adafruit MAX31856 driver also exposes `reference_temperature` and a fault
  dictionary.
- `probes/main.py:109-124` merges temperature dictionaries and has no health or
  validity side channel.
- `controller/runtime/modes/base.py:783-824` reads device status before the
  fresh probe sample, then passes the primary value into numeric safety guards
  and controller computation. A fault value cannot be represented safely as
  `None` until an explicit health guard precedes those numeric consumers.
- Microchip defines MCP9601 status register `0x04` bit `0x10` as open circuit and
  bit `0x20` as short circuit. Adafruit's Arduino MCP9601 header uses the same
  masks. The CircuitPython MCP9600 driver supports MCP9601 temperature reads but
  names only bit 4 as the MCP9600 `input_range` property and does not expose bit
  5.
- Playing With Fusion's SEN-30010 datasheet states that `SEN-30010-W` includes
  the required OC/SC sense circuitry, defaults to I2C address `0x61`, and
  supports bare-wire thermocouples.

Primary references:

- Microchip MCP960X datasheet:
  <https://ww1.microchip.com/downloads/en/DeviceDoc/MCP960X-L0X-RL0X-Data-Sheet-20005426F.pdf>
- Playing With Fusion SEN-30010 datasheet:
  <https://www.playingwithfusion.com/files/sen30010_datasheet_r01.pdf>
- Adafruit CircuitPython MCP9600 source:
  <https://github.com/adafruit/Adafruit_CircuitPython_MCP9600>
- Adafruit CircuitPython MAX31856 source:
  <https://github.com/adafruit/Adafruit_CircuitPython_MAX31856>

## Scope and delivery boundary

This specification records two related deliverables with different requested
end states.

### Current implementation scope

1. Add MCP9601 probe support, including the `SEN-30010-W` defaults.
2. Add opt-in MCP9601 hardware OC/SC detection, default off.
3. Add the typed health side channel and controller fault ordering needed for an
   enabled MCP9601 hardware fault to stop a primary probe safely.
4. Add wizard guidance for MCP9601 hardware detection and board compatibility.
5. Preserve all saved MCP9600 configurations and behavior.

### Design-only follow-up scope

1. Add shared inferred detection for MCP9600, MAX31856, MCP9601, and future
   thermocouple converters that expose both hot and cold junctions.
2. Add `off | observe | enforce` inference policy, default `observe`.
3. Add the conditional wizard warning for converters without enabled,
   board-supported hardware detection.

The inference engine is deliberately not part of the current MCP9601 code
change. Its design is complete here so the MCP9601 health contract does not
create an incompatible one-off path.

Out of scope: non-thermocouple probe families, calibration-drift detection,
reversed-polarity classification, intermittent-noise classification, location
validation, plant-model residuals, and automatic recovery of a primary
controller from Error.

## Shared health contract

Add a typed per-port report separate from the existing temperature dictionaries:

```text
ThermocoupleHealthReport
  state: unmonitored | healthy | suspected | confirmed
  faults: zero or more of open | short | malfunction
  evidence: zero or more of hardware | junction-collapse | stuck-response |
            excitation-response | implausible-step
  temperature_valid: bool
  observed_at: monotonic timestamp
  detail: structured evidence metrics
```

Reports are keyed by stable configured device and port identity, then projected
to the logical probe label for controller and UI consumption. Temperature
history remains `{primary, food, aux, tr}`; health metadata never appears as a
fake temperature group.

`ProbeInterface` gains a default no-report method so non-thermocouple drivers do
not need parallel implementations. `ProbesMain` collects each driver's latest
report and exposes it after a fresh read.

A confirmed report always has `temperature_valid = false`. The corresponding
output value is `None`; no last-known-good or cold-junction substitute is
presented as current sensor data.

## MCP9601 driver design

### Private MCP960x implementation

Add `probes/_mcp960x_adafruit.py` and make the public probe modules thin variants:

- `probes/mcp9600_adafruit.py`: existing module name, port `KTT0`, default
  address `0x67`, no MCP9601 status interpretation.
- `probes/mcp9601_adafruit.py`: port `KTT0`, default address `0x61`, optional
  MCP9601 status interpretation.

Both retain the existing thermocouple-type list and structured I2C bus support.
The private implementation owns unit conversion, output placement, cold-junction
sampling, and common construction. Public `ReadProbes` classes retain the dynamic
module-loader contract.

A local MCP9601 sensor subclass extends Adafruit's `MCP9600` class with read-only
register descriptors for status bits 4 and 5. It does not call Adafruit private
methods and does not patch the installed dependency.

### Configuration

The new `wizard/wizard_manifest.json` entry is
`modules.probes.mcp9601_adafruit`:

```text
i2c_bus_addr:               list 0x60..0x67, default 0x61
tc_type:                    B/E/J/K/N/R/S/T, default K
i2c_bus:                    structured I2C selector, default basic
hardware_fault_detection:  boolean, default false
transient:                  existing boolean convention, default false
```

The module description identifies `SEN-30010-W` as verified to include OC/SC
sense circuitry. It states that other MCP9601 boards require schematic
verification. The existing wizard image contract is retained; implementation
must provide or deliberately select an appropriate MCP9601 image rather than a
broken asset path.

Hardware detection remains opt-in even when the module is MCP9601. Enabling it
asserts that the installed board has the required VSENSE network. When disabled,
PiFire does not read or interpret OC/SC bits.

### Hardware-fault behavior

For every enabled hardware-detection sample:

1. Read status before accepting the hot-junction value.
2. Bit `0x10` adds confirmed `open`; bit `0x20` adds confirmed `short`. If
   both bits are present, `faults` preserves both rather than collapsing them
   into one display-oriented cause.
3. An asserted bit invalidates the current temperature immediately. Direct
   hardware evidence does not require software-inference agreement.
4. A clean status produces a healthy hardware report and allows hot/cold reads.
   A clean hardware channel does not override inferred evidence when the future
   fusion engine is enabled.
5. Food/aux hardware faults clear only after 60 consecutive seconds of clean
   reports. Primary faults remain latched through the Error transition and reset
   only on explicit device/cook initialization after operator correction.

I2C read exceptions remain device-read failures; they are not mislabeled as
thermocouple OC/SC conditions.

## Controller safety ordering

After both the initial probe read and every in-loop probe read, ordering is:

```text
fresh probe read
→ collect current thermocouple health
→ confirmed-primary-fault guard
→ existing max-temperature/flameout guards
→ controller computation
→ physical actuation
```

The current device-info/status projection moves after health collection so the
frontend and controller observe the same sample rather than status from the
previous loop iteration.

The new guard is universal and has priority over guards that require a numeric
primary value. On a confirmed primary fault it requests Mode.ERROR, records the
fault kind/evidence, emits the probe-fault notification, and exits before
actuation. It never allows `None` to reach `over_max_temp`, flameout comparison,
or a controller.

Confirmed food/aux faults set only that logical value to `None`, emit one
notification on the transition into confirmed, and continue combustion control.
Repeated samples in the same state do not repeat notifications.

## Inferred detector design

### Ownership and sampling

The future shared engine consumes Celsius-domain thermocouple samples and a
small controller-neutral excitation context:

```text
active cook mode
primary setpoint
delivered igniter/auger heat-on seconds
healthy witness-probe temperatures
```

It stores one sample per second in a fixed 301-entry ring per thermocouple. A
window is usable only with at least 240 seconds of coverage and no sample gap
over 30 seconds. Clock time is injected for deterministic tests.

MAX31856 maps `reference_temperature` to cold junction. MCP9600/MCP9601 map
`ambient_temperature`. Drivers lacking a cold-junction reading remain
`unmonitored` by this inference policy.

This policy does not apply to thermistors through ADS1015/ADS1115, MAX31865
RTDs, DS18B20, Bluetooth probes, or cloud probes. Those sensor families do not
expose the hot/cold-junction relationship and require separate fault models.

### Channel 1: sensor-internal anomaly

The engine computes these candidates after a complete five-minute window:

- **Junction collapse:** at least 95% of samples satisfy
  `abs(hot_c - cold_c) <= 1.0`, and the hot-minus-cold span is at most `1.0°C`.
- **Stuck response:** hot-junction span is at most `1.0°C` while the independent
  witness defined below has materially warmed.

Outside a slow-path identification opportunity, these metrics are diagnostic
only: they cannot move health to either `suspected` or `confirmed`. A flat
probe at setpoint and ordinary maintenance auger pulses therefore cannot
trigger an inferred state transition.

### Fast path: live junction collapse

The five-minute path covers a probe already disconnected when a ramp begins.
A probe that disconnects during a cook has stronger temporal evidence and must
not drive maximum heat for five minutes. Confirm a live collapse when all of
these hold:

1. Cook mode is active.
2. Before the event, the probe had a valid hot-minus-cold separation of at
   least `15°C`.
3. Hot temperature falls by at least `20°C` within 10 seconds.
4. The next five one-second samples all satisfy
   `abs(hot_c - cold_c) <= 1.0°C`.

The physically implausible step and the electrical junction-collapse signature
are the two agreeing mechanisms. This path cannot trigger from a steady
setpoint: maintenance has neither the step nor the collapse. A lid opening may
cool the pit, but it does not make a connected thermocouple equal the
cold-junction sensor within five seconds. Deliberately removing the primary
probe during an active cook is correctly treated as loss of the control sensor.

### Channel 2: verified excitation mismatch

A window is an identification opportunity only when all conditions hold:

1. Cook mode is active.
2. At window start, setpoint exceeds candidate hot temperature by at least
   `15°C`.
3. Delivered igniter/auger activity totals at least 30 seconds.
4. An independent witness proves warming:
   - preferred: another healthy chamber probe rises by at least `10°C`; or
   - fallback: the converter cold junction rises by at least `3°C`.

The response channel asserts when the verified ramp occurs but the candidate
fails to respond:

- with a peer witness, candidate hot rise is under `3°C`; or
- with the cold-junction witness, hot-minus-cold separation grows by under
  `2°C`.

Commanded heat without a warming witness is insufficient evidence. It may be
failed ignition, no fuel, or another plant fault; it never confirms a
thermocouple failure.

### Fusion, authority, and recovery

- One slow-path channel in an eligible identification window: `suspected`;
  status visibility only.
- Both slow-path channels in the same eligible window: `confirmed` inferred
  malfunction.
- Both fast-path signatures: `confirmed` live junction collapse.
- Enabled hardware OC/SC: `confirmed` without inference.
- `observe`: default. Compute states and metrics; notify once on transition to
  confirmed, but never change controller mode.
- `enforce`: confirmed primary transitions to Error before actuation; confirmed
  food/aux remains notify-and-unavailable.
- `off`: no inference allocation or evaluation.

Suspected does not notify. Confirmed does. Observe mode therefore surfaces
high-confidence faults without introducing a default automatic stop. Users who
select enforce explicitly authorize the two-channel primary safety action.

A primary confirmation is latched through Error. Food/aux inferred confirmation
requires 60 consecutive seconds of non-anomalous samples to clear. With no new
identification opportunity, elapsed time alone cannot increase or clear
slow-path confidence. The explicitly defined live-collapse fast path is the
only inference path outside a verified ramp.

### Wizard warning

Use the existing wizard `pf-module-notes` warning presentation in the probe
device form. Show the warning whenever:

```text
module device_specific.type == thermocouple
AND
(the module has no hardware_fault_detection field
 OR the current hardware_fault_detection value is not true)
```

Warning text:

> WARNING: This amplifier does not have enabled, board-supported thermocouple
> fault detection. A disconnected or electrically shorted/collapsed probe may
> read as the cold-junction (ambient) temperature instead of reporting a fault.
> Software thermocouple fault detection is STRONGLY RECOMMENDED.

This is manifest/type-driven rather than keyed to MCP9600, MAX31856, or MCP9601
module names. Default `observe` satisfies the recommendation without changing
control mode. The warning remains useful because observe notifies but does not
stop; selecting `enforce` is an explicit safety choice.

Until the inference follow-up is implemented, the current MCP9601 delivery must
not claim that software detection is available. Its wizard note instead states
that disabled hardware detection cannot identify OC/SC faults and that the
installed board must support VSENSE before enabling it. The full warning above
ships with the inference setting.

## Diagnostics and observability

Health state transitions carry structured evidence rather than only prose:

```text
policy version
window duration and coverage
hot, cold, and delta spans
junction-collapse fraction
heat-on seconds
witness source and temperature rise
asserted evidence channels
hardware status byte when read
```

Observe mode records a confirmed transition and sends its notification. It does
not persist every one-second sample. Repeated identical states are deduplicated.
The current report is projected through probe device status for operator
inspection.

## Thermocouple failure signatures and coverage

- **Open input or TC+ shorted to TC-:** commonly collapses thermocouple EMF so
  hot follows cold junction. Covered by hardware OC when available, the live
  fast path, or the verified-ramp slow path. A wire-to-wire short is not the
  MCP9601 short-to-supply condition and may not assert its SC bit.
- **Short to VDD or ground:** covered by enabled, board-supported MCP9601
  hardware detection. Without trustworthy hardware support it may instead
  saturate, report out of range, or produce a biased value; the inference
  engine does not claim universal classification.
- **Converter stuck at an arbitrary value:** covered only when the stuck
  internal channel and independent verified-ramp response channel agree.
- **Reversed polarity, intermittent contact, leakage/moisture, damaged
  insulation, grounded-probe common-mode violations, and wrong thermocouple
  type:** can produce reversed, noisy, biased, or location-dependent readings
  rather than ambient. They are recorded as known failure signatures, but this
  detector does not claim to confirm all of them.

The UI and documentation must therefore describe this as open/collapse/stuck
detection, not universal proof that every thermocouple malfunction is detected.

## Alternatives rejected

### Driver-local hot/cold heuristic

Rejected because equality and slope tests derive from the same signals, lack
actual heat-demand context, and can confuse rest conditions or failed ignition
with disconnection.

### Learned plant-model residual

Rejected for this detector because fuel, weather, failed ignition, lid state,
and model error can all create residuals. It also couples safety behavior to a
specific controller family. It may become a third advisory channel later.

### Last-known-good or cold-junction substitution

Rejected because it presents stale or known-invalid data as current and could
allow a primary controller to actuate from a fabricated temperature.

## Verification contracts

### Current MCP9601 implementation

- Manifest test: module exists; address defaults to `0x61`; hardware detection
  defaults false; type and I2C fields match existing conventions.
- Driver tests: construction, all thermocouple types, structured I2C bus, clean
  status, open, short, both bits, disabled detection, I2C failure, and 60-second
  food/aux recovery.
- Regression tests: existing MCP9600 defaults, module loading, temperature
  conversion, and manifest remain unchanged.
- Safety tests: initial-read and in-loop confirmed primary faults request Error
  before fake actuation; `None` never reaches numeric safety functions.
- Secondary tests: food/aux fault becomes unavailable, notifies once, and does
  not stop combustion.
- Wizard tests: the MCP9601 board-compatibility note and default-off control are
  visible.
- Focused branch coverage for new or substantially rewritten modules exceeds
  90%.

A real-board smoke check, when `SEN-30010-W` hardware is available, reads at
`0x61`, verifies clean temperature, disconnects the thermocouple to observe the
OC bit, and performs only a vendor-approved current-limited short-to-rail test.
No automated test creates a power-supply short.

### Future inference implementation

Deterministic sequence tests cover rest, healthy setpoint maintenance with auger
pulses, valid ramp, startup-disconnected ramp, live disconnection at setpoint,
lid-open cooling without cold-junction collapse, stuck arbitrary value, failed
ignition with no warming witness, sample gaps, Celsius and Fahrenheit UI
settings, peer-witness priority, cold-junction fallback, observe notification
without stop, enforce primary stop before actuation, food/aux continuation,
latching, recovery, and warning visibility.

Threshold boundary tests exercise values immediately below, at, and above every
policy constant. Each test defends the externally visible health state,
notification, validity, or controller transition—not internal deque plumbing.
