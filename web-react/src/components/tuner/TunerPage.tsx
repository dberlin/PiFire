import { useCallback, useEffect, useRef, useState } from "react";
import { getSettings } from "../../helpers/settings/settingsApi";
import { computeCoefficients, fetchTr, tunerErrorText } from "../../helpers/tuner/tunerApi";
import type { Coefficients, Segment, TrReading } from "../../helpers/tuner/tunerTypes";
import { useTunerSession } from "../../helpers/tuner/useTunerSession";
import { ProfileForm } from "./ProfileForm";
import { SegmentCard } from "./SegmentCard";
import { TunerChart } from "./TunerChart";
import "./tuner.css";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// Flask's tunerUpdateTr polls at this cadence. The reading is refreshed by the
// control loop, so a faster poll only spends requests to read the same value.
const POLL_MS = 1000;

const SEGMENTS: Segment[] = ["High", "Medium", "Low"];

type RecordedPoint = { temp: number; trohms: number };

/** The probe labels the operator can tune, read off the live probe map. */
function probeLabels(settings: unknown): string[] {
  const info =
    (settings as { probe_settings?: { probe_map?: { probe_info?: unknown[] } } })?.probe_settings
      ?.probe_map?.probe_info ?? [];
  return info
    .map((p) => (p as { label?: unknown }).label)
    .filter((label): label is string => typeof label === "string");
}

/**
 * The probe tuner, manual flow.
 *
 * Nothing here reads or writes the grill until the operator presses Start:
 * mounting the page and choosing a probe is not consent to move the grill into
 * Monitor. Once a session is open the page polls the selected probe's
 * resistance once a second and the three cards record a temperature against it.
 * Finish solves for the coefficients, closes the session, and offers the curve
 * and the save form.
 */
export function TunerPage() {
  const [probes, setProbes] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [reading, setReading] = useState<TrReading | null>(null);
  const [recorded, setRecorded] = useState<Record<Segment, RecordedPoint | null>>({
    High: null,
    Medium: null,
    Low: null,
  });
  const [coefficients, setCoefficients] = useState<Coefficients | null>(null);
  const [finishError, setFinishError] = useState<string | null>(null);

  const session = useTunerSession(BASE_URL);

  //  Read the probe list once on mount. No session, no control write -- just
  //  the map the operator picks from.
  useEffect(() => {
    let cancelled = false;
    getSettings(BASE_URL).then((settings) => {
      if (cancelled) return;
      const labels = probeLabels(settings);
      setProbes(labels);
      setSelected((current) => current || labels[0] || "");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  //  Poll the selected probe while the session is open. The timer is armed only
  //  after a read settles (see inFlight), so a slow response cannot queue polls
  //  behind itself; it stops the instant the session leaves "open".
  const inFlight = useRef(false);
  useEffect(() => {
    if (session.status !== "open" || !selected) return;
    let cancelled = false;

    const poll = async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      const result = await fetchTr(selected, BASE_URL);
      inFlight.current = false;
      if (!cancelled && result.ok && result.data) setReading(result.data);
    };

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [session.status, selected]);

  const record = useCallback((segment: Segment, temp: number, trohms: number) => {
    setRecorded((prev) => ({ ...prev, [segment]: { temp, trohms } }));
  }, []);

  const clear = useCallback((segment: Segment) => {
    setRecorded((prev) => ({ ...prev, [segment]: null }));
  }, []);

  const allRecorded = SEGMENTS.every((s) => recorded[s] !== null);

  const finish = useCallback(async () => {
    const points = SEGMENTS.map((segment) => {
      const point = recorded[segment];
      // allRecorded gates the button, so point is non-null here.
      return { segment, temp: point?.temp ?? 0, trohms: point?.trohms ?? 0 };
    });
    setFinishError(null);
    const result = await computeCoefficients(points, BASE_URL);
    if (result.ok && result.data) {
      setCoefficients(result.data);
      //  The readings are captured; the grill has no more work to do, so hand
      //  it back to Stop before the operator names the profile.
      session.stop();
    } else {
      setFinishError(tunerErrorText(result));
    }
  }, [recorded, session]);

  // The results view: the curve and the save form, after a successful Finish.
  if (coefficients) {
    return (
      <div className="pf-tuner">
        <header className="pf-tuner-header">
          <h1 className="pf-tuner-title">Tuner</h1>
        </header>
        <TunerChart chart={coefficients.chart} chartOk={coefficients.chart_ok} />
        <ProfileForm
          coefficients={{ a: coefficients.a, b: coefficients.b, c: coefficients.c }}
          probeLabel={selected}
          onSaved={() => setCoefficients(null)}
        />
      </div>
    );
  }

  return (
    <div className="pf-tuner">
      <header className="pf-tuner-header">
        <h1 className="pf-tuner-title">Tuner</h1>
        <label className="pf-tuner-field-label" htmlFor="tuner-probe">
          Probe
        </label>
        <select
          id="tuner-probe"
          className="pf-tuner-input"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={session.status === "open"}
        >
          {probes.map((label) => (
            <option key={label} value={label}>
              {label}
            </option>
          ))}
        </select>
        {session.status === "open" ? (
          <button type="button" className="pf-tuner-btn" onClick={session.stop}>
            Stop tuning
          </button>
        ) : (
          <button
            type="button"
            className="pf-tuner-btn"
            onClick={session.start}
            disabled={!selected || session.status === "opening"}
          >
            Start tuning
          </button>
        )}
      </header>

      {session.error && (
        <p className="pf-tuner-error" role="alert">
          {session.error}
        </p>
      )}
      {finishError && (
        <p className="pf-tuner-error" role="alert">
          {finishError}
        </p>
      )}

      <div className="pf-tuner-segments">
        {SEGMENTS.map((segment) => (
          <SegmentCard
            key={segment}
            segment={segment}
            reading={session.status === "open" ? reading : null}
            recorded={recorded[segment]}
            onRecord={(temp, trohms) => record(segment, temp, trohms)}
            onClear={() => clear(segment)}
          />
        ))}
      </div>

      <button
        type="button"
        className="pf-tuner-btn"
        disabled={!allRecorded}
        onClick={() => void finish()}
      >
        Finish
      </button>
    </div>
  );
}
