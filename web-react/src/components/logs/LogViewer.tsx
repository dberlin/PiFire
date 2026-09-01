import { LazyLog } from "@melloware/react-logviewer";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchLogDelta, fetchLogWhole } from "../../helpers/logs/logsApi";

import "./logs.css";

const POLL_MS = 3000;

/** Colourize the level tag. LazyLog calls this per line part. */
function formatPart(text: string) {
  if (text.includes("[ERROR]")) return <span className="pf-log-error">{text}</span>;
  if (text.includes("[WARNING]")) return <span className="pf-log-warning">{text}</span>;
  return text;
}

/**
 * One log family, virtualized and searchable.
 *
 * LazyLog runs in `external` mode: this component owns the fetching so the tail
 * can go through the Range endpoint, and pushes new lines in through
 * appendLines() on a ref. Letting LazyLog fetch its own `url` would re-download
 * the whole family on every poll -- which for a rotated events family is up to
 * 4 MiB every three seconds.
 */
export function LogViewer({ stem, follow }: { stem: string; follow: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(stem);
  const logRef = useRef<LazyLog | null>(null);
  const offset = useRef(0);
  const total = useRef(0);

  //  Switching families has to clear what is on screen, or the previous log's
  //  content sits under the new one's name until the read lands -- on a grill,
  //  reading someone else's errors as your own. Adjusted during render rather
  //  than in the effect below: React re-renders before committing, so nothing
  //  stale ever reaches the DOM, and a synchronous setState inside an effect
  //  is a cascading render (react-hooks/set-state-in-effect).
  if (shown !== stem) {
    setShown(stem);
    setText(null);
    setError(null);
  }

  const load = useCallback(async () => {
    const whole = await fetchLogWhole(stem);
    offset.current = whole.total;
    total.current = whole.total;
    return whole.text;
  }, [stem]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((loaded) => {
        if (!cancelled) setText(loaded);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    if (!follow || text === null) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const delta = await fetchLogDelta(stem, offset.current, total.current);
        if (cancelled) return;
        offset.current = delta.nextOffset;
        total.current = delta.total;
        if (delta.kind === "appended" && delta.text) {
          logRef.current?.appendLines(delta.text.replace(/\n$/, "").split("\n"));
        } else if (delta.kind === "rotated") {
          //  The stream shrank underneath us, so every cached line may be
          //  stale. Replace wholesale rather than appending onto wrong content.
          setText(delta.text);
        }
      } catch {
        //  A failed poll is not fatal; the next tick retries. The grill going
        //  briefly unreachable must not tear down a viewer the user is reading.
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [follow, stem, text]);

  if (error) {
    return (
      <p className="pf-settings-error-text" role="alert">
        {error}
      </p>
    );
  }
  if (text === null) return <p className="pf-admin-note">Loading log…</p>;

  return (
    <div className="pf-log-frame">
      <LazyLog
        ref={logRef}
        text={text}
        external
        enableSearch
        enableSearchNavigation
        caseInsensitive
        enableLineNumbers
        selectableLines
        wrapLines
        follow={follow}
        formatPart={formatPart}
        //  "auto" is the only string this accepts. Every other value goes
        //  through Number(height), so a CSS length like "60vh" becomes NaN and
        //  the list renders zero rows -- search bar and all, silently. The
        //  height comes from .pf-log-frame instead.
        height="auto"
        extraLines={1}
      />
    </div>
  );
}
