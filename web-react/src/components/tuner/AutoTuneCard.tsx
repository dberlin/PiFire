import type { AutoStatus } from "@pifire/core/contracts/operations";
import "./tuner.css";

/**
 * The auto-tuning accumulation card, ported from _macro_tuner.html's
 * render_auto_tool.
 *
 * Auto tuning does not ask the operator to type temperatures: it reads a
 * REFERENCE probe (one with a trusted profile) as the grill's temperature
 * drifts, pairing each reading with the tuned probe's resistance, until the
 * spread is wide enough to solve. This card picks the reference and shows the
 * live readings and the running progress; it does NOT own the session or the
 * Finish button -- the page does.
 *
 * A null reading reads "Waiting…", never 0: an absent probe is not reporting,
 * which is different from a probe measuring a real zero.
 */
export function AutoTuneCard({
  probes,
  reference,
  onReferenceChange,
  tuneProbe,
  status,
  active,
}: {
  probes: string[];
  reference: string;
  onReferenceChange: (label: string) => void;
  tuneProbe: string;
  status: AutoStatus | null;
  active: boolean;
}) {
  const temp = status?.current_temp ?? null;
  const trohms = status?.current_tr ?? null;

  return (
    <section className="pf-tuner-auto" aria-labelledby="tuner-auto-title">
      <h3 className="pf-tuner-segment-title" id="tuner-auto-title">
        Auto tune {tuneProbe}
      </h3>

      <label className="pf-tuner-field-label" htmlFor="tuner-auto-reference">
        Reference probe
      </label>
      <select
        id="tuner-auto-reference"
        className="pf-tuner-input"
        value={reference}
        onChange={(e) => onReferenceChange(e.target.value)}
        disabled={active}
      >
        {probes.map((label) => (
          <option key={label} value={label}>
            {label}
          </option>
        ))}
      </select>

      {status && (
        <>
          <div className="pf-tuner-auto-readings">
            <div className="pf-tuner-auto-reading">
              <span className="pf-tuner-field-label">Reference temp</span>
              <span className="pf-tuner-reading">{temp === null ? "Waiting…" : `${temp}°`}</span>
            </div>
            <div className="pf-tuner-auto-reading">
              <span className="pf-tuner-field-label">{tuneProbe} resistance</span>
              <span className="pf-tuner-reading">
                {trohms === null ? "Waiting…" : `${trohms} Ω`}
              </span>
            </div>
          </div>

          <p className="pf-tuner-auto-progress" role="status">
            {status.ready
              ? `Ready — ${status.samples} samples span a wide enough range to build a profile.`
              : `Collecting samples (${status.samples} so far). Let the grill's temperature keep moving until the range is wide enough.`}
          </p>
        </>
      )}
    </section>
  );
}
