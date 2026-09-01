import type { Segment, TrReading } from "@pifire/core/contracts/operations";
import { useState } from "react";

import "./tuner.css";

/**
 * One of the three manual-tuning points (High / Medium / Low), ported from
 * _macro_tuner.html's render_manual_tool_card.
 *
 * The operator holds the probe at a temperature, reads a thermometer, and
 * records that temperature against the resistance the probe is reporting right
 * now. The live reading and a typed temperature become one recorded pair.
 *
 * A null reading is shown as "waiting", never as 0: a shorted probe reports a
 * real 0 ohms, and recording a point off a probe that is not reporting would
 * poison the whole solve. Record stays disabled until there is a reading to
 * freeze AND a temperature to pair with it.
 */
export function SegmentCard({
  segment,
  reading,
  recorded,
  onRecord,
  onClear,
}: {
  segment: Segment;
  reading: TrReading | null;
  recorded: { temp: number; trohms: number } | null;
  onRecord: (temp: number, trohms: number) => void;
  onClear: () => void;
}) {
  const [temp, setTemp] = useState("");

  const trohms = reading?.trohms ?? null;
  const reporting = trohms !== null;
  const stale = reading != null && !reading.tuning;
  const tempValue = temp.trim() === "" ? Number.NaN : Number(temp);
  const canRecord = reporting && Number.isFinite(tempValue);

  const inputId = `tuner-temp-${segment}`;

  return (
    <section className="pf-tuner-segment" aria-labelledby={`tuner-seg-${segment}`}>
      <h3 className="pf-tuner-segment-title" id={`tuner-seg-${segment}`}>
        {segment}
      </h3>

      {recorded ? (
        <div className="pf-tuner-recorded">
          <p className="pf-tuner-recorded-value">
            {recorded.temp}° at {recorded.trohms} Ω
          </p>
          <button type="button" className="pf-tuner-btn" onClick={onClear}>
            Clear
          </button>
        </div>
      ) : (
        <>
          <p className="pf-tuner-reading">{reporting ? `${trohms} Ω` : "Waiting for a reading…"}</p>
          {stale && (
            <p className="pf-tuner-stale" role="status">
              The grill is not updating this reading — start tuning first.
            </p>
          )}
          <label className="pf-tuner-field-label" htmlFor={inputId}>
            Temperature
          </label>
          <input
            id={inputId}
            className="pf-tuner-input"
            type="number"
            inputMode="decimal"
            value={temp}
            onChange={(e) => setTemp(e.target.value)}
          />
          <button
            type="button"
            className="pf-tuner-btn"
            disabled={!canRecord}
            onClick={() => {
              if (canRecord && trohms !== null) onRecord(tempValue, trohms);
            }}
          >
            Record
          </button>
        </>
      )}
    </section>
  );
}
