import { useCallback, useEffect, useRef, useState } from "react";
import { getSettings } from "../../helpers/settings/settingsApi";
import {
  computeCoefficients,
  fetchAutoStatus,
  fetchTr,
  tunerErrorText,
} from "../../helpers/tuner/tunerApi";
import type {
  AutoStatus,
  Coefficients,
  Segment,
  TrReading,
  TunerPoint,
} from "../../helpers/tuner/tunerTypes";
import { useTunerSession } from "../../helpers/tuner/useTunerSession";
import { AutoTuneCard } from "./AutoTuneCard";
import { ProfileForm } from "./ProfileForm";
import { SegmentCard } from "./SegmentCard";
import { TunerChart } from "./TunerChart";
import "./tuner.css";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// Flask's tunerUpdateTr polls at this cadence. The reading is refreshed by the
// control loop, so a faster poll only spends requests to read the same value.
const POLL_MS = 1000;

const SEGMENTS: Segment[] = ["High", "Medium", "Low"];

type TuneMode = "manual" | "auto";
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
 * The probe tuner: manual and auto flows over one session and one solve.
 *
 * Nothing here reads or writes the grill until the operator presses Start:
 * mounting the page and choosing a probe is not consent to move the grill into
 * Monitor. In MANUAL mode the operator holds the probe at three temperatures
 * and records each against the live resistance. In AUTO mode a reference probe
 * supplies the temperature and the page accumulates samples until the spread is
 * wide enough. Either way, Finish sends three temperature/resistance points to
 * the same solve, closes the session, and offers the curve and the save form.
 */
export function TunerPage() {
  const [mode, setMode] = useState<TuneMode>("manual");
  const [probes, setProbes] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [reference, setReference] = useState("");
  const [reading, setReading] = useState<TrReading | null>(null);
  const [autoStatus, setAutoStatus] = useState<AutoStatus | null>(null);
  const [recorded, setRecorded] = useState<Record<Segment, RecordedPoint | null>>({
    High: null,
    Medium: null,
    Low: null,
  });
  const [coefficients, setCoefficients] = useState<Coefficients | null>(null);
  const [finishError, setFinishError] = useState<string | null>(null);

  const session = useTunerSession(BASE_URL);
  const open = session.status === "open";

  //  Read the probe list once on mount. No session, no control write -- just
  //  the map the operator picks from. Reference defaults to the first probe
  //  that is not the tune target -- auto tuning reads a DIFFERENT, trusted probe.
  useEffect(() => {
    let cancelled = false;
    getSettings(BASE_URL).then((settings) => {
      if (cancelled) return;
      const labels = probeLabels(settings);
      setProbes(labels);
      setSelected((current) => current || labels[0] || "");
      setReference((current) => current || labels.find((l) => l !== labels[0]) || labels[0] || "");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  //  Poll while the session is open. The timer is armed only after a read
  //  settles (see inFlight), so a slow response cannot queue polls behind
  //  itself; it stops the instant the session leaves "open". Manual polls the
  //  tuned probe's resistance; auto records a sample against the reference and
  //  reads back the running selection.
  const inFlight = useRef(false);
  useEffect(() => {
    if (!open || !selected) return;
    if (mode === "auto" && !reference) return;
    let cancelled = false;

    const poll = async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      if (mode === "manual") {
        const result = await fetchTr(selected, BASE_URL);
        if (!cancelled && result.ok && result.data) setReading(result.data);
      } else {
        const result = await fetchAutoStatus(selected, reference, BASE_URL);
        if (!cancelled && result.ok && result.data) setAutoStatus(result.data);
      }
      inFlight.current = false;
    };

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, selected, reference, mode]);

  const record = useCallback((segment: Segment, temp: number, trohms: number) => {
    setRecorded((prev) => ({ ...prev, [segment]: { temp, trohms } }));
  }, []);

  const clear = useCallback((segment: Segment) => {
    setRecorded((prev) => ({ ...prev, [segment]: null }));
  }, []);

  const allRecorded = SEGMENTS.every((s) => recorded[s] !== null);
  const canFinish = mode === "manual" ? allRecorded : Boolean(autoStatus?.ready);

  const finish = useCallback(async () => {
    let points: TunerPoint[];
    if (mode === "manual") {
      points = SEGMENTS.map((segment) => {
        const point = recorded[segment];
        // allRecorded gates the button, so point is non-null here.
        return { segment, temp: point?.temp ?? 0, trohms: point?.trohms ?? 0 };
      });
    } else {
      //  The three points the server picked out of the accumulated samples.
      //  ready gates the button, so autoStatus is present.
      const s = autoStatus;
      points = [
        { segment: "High", temp: s?.high_temp ?? 0, trohms: s?.high_tr ?? 0 },
        { segment: "Medium", temp: s?.medium_temp ?? 0, trohms: s?.medium_tr ?? 0 },
        { segment: "Low", temp: s?.low_temp ?? 0, trohms: s?.low_tr ?? 0 },
      ];
    }
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
  }, [mode, recorded, autoStatus, session]);

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

        {/* The flow choice. Disabled while a session is open: switching mode
            mid-tune would abandon whatever the current flow has gathered. */}
        <fieldset className="pf-tuner-mode" aria-label="Tuning mode">
          {(["manual", "auto"] as const).map((m) => (
            <button
              key={m}
              type="button"
              className={`pf-tuner-mode-btn ${mode === m ? "on" : ""}`}
              aria-pressed={mode === m}
              disabled={open}
              onClick={() => setMode(m)}
            >
              {m === "manual" ? "Manual" : "Auto"}
            </button>
          ))}
        </fieldset>

        <label className="pf-tuner-field-label" htmlFor="tuner-probe">
          Probe
        </label>
        <select
          id="tuner-probe"
          className="pf-tuner-input"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={open}
        >
          {probes.map((label) => (
            <option key={label} value={label}>
              {label}
            </option>
          ))}
        </select>
        {open ? (
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

      {mode === "manual" ? (
        <div className="pf-tuner-segments">
          {SEGMENTS.map((segment) => (
            <SegmentCard
              key={segment}
              segment={segment}
              reading={open ? reading : null}
              recorded={recorded[segment]}
              onRecord={(temp, trohms) => record(segment, temp, trohms)}
              onClear={() => clear(segment)}
            />
          ))}
        </div>
      ) : (
        <AutoTuneCard
          probes={probes}
          reference={reference}
          onReferenceChange={setReference}
          tuneProbe={selected}
          status={open ? autoStatus : null}
          active={open}
        />
      )}

      <button
        type="button"
        className="pf-tuner-btn"
        disabled={!canFinish}
        onClick={() => void finish()}
      >
        Finish
      </button>
    </div>
  );
}
