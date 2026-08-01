import { LazyLog } from "@melloware/react-logviewer";
import { useEffect, useRef, useState } from "react";
import { formatInstallLog } from "../../helpers/wizard/installLog";
import { getInstallLog } from "../../helpers/wizard/wizardApi";
import "../logs/logs.css";

// The status line above is polled four times a second because it is a progress
// readout. The transcript is not: it is served incrementally, so a slower tick
// loses nothing -- it just arrives in bigger pieces.
const POLL_MS = 1000;

/**
 * The installer's command output, live.
 *
 * Built on LazyLog in `external` mode, the same way the events page's LogViewer
 * is, so an install that logs thousands of lines is virtualized and searchable
 * rather than rendered whole. It does not reuse LogViewer itself because that
 * component is bound to the admin logs API, which serves a log FAMILY: a
 * `fetchLogWhole("wizard")` pulls wizard.log plus its three rotated backups --
 * up to 4 MiB, over the wifi of a Pi that is mid-install -- and every previous
 * install in them would render above this one. /api/wizard/installlog serves
 * the current run only.
 *
 * Mounted only while the disclosure is open, so a closed panel costs no polls.
 * Nothing is lost by that: the endpoint reads from a byte offset, so the first
 * fetch after opening returns the run from its beginning however late it is.
 */
export function InstallOutput({ baseUrl }: { baseUrl: string }) {
  const [text, setText] = useState("");
  // Bumped on every reset, and used as LazyLog's key. LazyLog ingests `text`
  // when it mounts and thereafter owns its own rows -- appendLines() is the
  // only way in -- so a later `text` prop is quietly ignored. Re-keying is what
  // makes a replacement actually replace.
  const [generation, setGeneration] = useState(0);
  const logRef = useRef<LazyLog | null>(null);
  // Bytes already consumed. A ref, not state: it changes on every poll, nothing
  // renders from it, and as state it would restart the polling effect each tick.
  const offsetRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      const from = offsetRef.current;
      getInstallLog(baseUrl, from)
        .then((next) => {
          if (cancelled) return;
          // Discard a response computed from an offset already moved past. Two
          // reads can overlap -- a slow one plus the next tick, or a remount --
          // and the late one carries lines the earlier one already delivered,
          // so applying it duplicates them.
          if (offsetRef.current !== from) return;
          offsetRef.current = next.offset;
          // `reset` means the offset no longer points into the run on screen --
          // a second install, or rotation. Appending would splice one run's
          // tail onto another's head, so replace instead.
          if (next.reset) {
            setText(formatInstallLog(next.text));
            setGeneration((n) => n + 1);
          } else if (next.text) {
            const formatted = formatInstallLog(next.text);
            // Before the first line lands there is no viewer to append to, so
            // the text goes into state and mounts one.
            if (logRef.current)
              logRef.current.appendLines(formatted.replace(/\n$/, "").split("\n"));
            else setText((prev) => prev + formatted);
          }
        })
        // A dropped transcript poll is not worth surfacing: the status line is
        // the authority on whether the install is alive, and it polls
        // separately. The next tick retries.
        .catch(() => {});
    };
    pull();
    const id = window.setInterval(pull, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [baseUrl]);

  // Held back until there is a line to show. Mounting LazyLog on placeholder
  // text would bake that placeholder in as row one for the rest of the install,
  // and swallow the first read along with it.
  if (!text)
    return <p className="pf-install-output-waiting">Waiting for the installer's first line…</p>;

  return (
    <div className="pf-log-frame pf-install-output-frame">
      <LazyLog
        key={generation}
        ref={logRef}
        text={text}
        external
        enableSearch
        caseInsensitive
        selectableLines
        wrapLines
        // The point of the panel is the newest line, and an install is not
        // something you read from the top.
        follow
        // "auto" is the only string this accepts -- every other value goes
        // through Number(height) and a CSS length becomes NaN, which renders
        // zero rows. The height comes from .pf-install-output-frame.
        height="auto"
        extraLines={1}
      />
    </div>
  );
}
