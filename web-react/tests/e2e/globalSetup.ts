// Refuses to run the suite against a backend that is serving code the checkout
// no longer has.
//
// A gunicorn worker keeps whatever it imported when it forked. A server started
// before a commit answers 404 for every route added since, and the specs that
// need them fail as though the FRONTEND were broken. That has cost five separate
// tasks in this repo; the most recent one produced a page of geometry diffs that
// were confidently misdiagnosed as browser drift, and the real cause -- a worker
// 37 minutes older than the routes -- was only found by comparing process start
// time to commit time by hand.
//
// The check is deliberately one-sided. The demo-server projects (fidelity,
// fidelity-pages, fidelity-chrome, reflow, panel) stub every fetch and do not
// need PiFire at all, so an unreachable backend is NOT an error -- running them
// on a machine with no grill is legitimate. A backend that answers and reports
// itself stale IS an error, because anything measured against it is untrustworthy
// and the failure will not look like what it is.

import { ports } from "../../ports";

interface VersionsPayload {
  data?: {
    revision?: string | null;
    stale?: boolean;
    started_at?: number;
    newest_source_mtime?: number;
  };
}

const RESTART = [
  "  Reload it gracefully (no downtime, no dropped datastore):",
  "",
  "    kill -HUP $(pgrep -f 'gunicorn.*app:app' | head -1)",
  "",
  "  control.py does NOT need restarting -- it is a separate process and this",
  "  check says nothing about it.",
].join("\n");

export default async function globalSetup() {
  const url = `${ports.pifireUrl}/api/get/revision`;

  let payload: VersionsPayload;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) {
      console.warn(`[globalSetup] ${url} answered ${res.status}; skipping the staleness check.`);
      return;
    }
    payload = (await res.json()) as VersionsPayload;
  } catch {
    //  No backend. Legitimate for the stubbed demo projects, so this is a note,
    //  not a failure -- the app/pellets projects will fail on their own terms
    //  with a clearer message than anything invented here.
    console.warn(`[globalSetup] no PiFire at ${ports.pifireUrl}; skipping the staleness check.`);
    return;
  }

  //  An older PiFire has no `stale` field. Absent means "cannot tell", which
  //  must not block a run -- this guard may not have shipped on that build.
  if (payload.data?.stale !== true) return;

  const started = payload.data.started_at;
  const newest = payload.data.newest_source_mtime;
  //  Seconds below a minute: "0 minutes" reads as "no drift", which is the
  //  opposite of what this message exists to say.
  const drift = (() => {
    if (!started || !newest) return "at an unknown time";
    const seconds = Math.max(0, Math.round(newest - started));
    if (seconds < 60) return `${seconds}s after it started`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} minutes after it started`;
    return `${(seconds / 3600).toFixed(1)} hours after it started`;
  })();

  throw new Error(
    [
      "",
      "The PiFire backend is running stale code.",
      "",
      `  ${ports.pifireUrl} loaded Python that has since changed on disk -- modified ${drift}.`,
      `  Its revision at import: ${payload.data.revision ?? "unknown"}`,
      "",
      "  Every route added since then answers 404, which reads as a broken",
      "  frontend rather than a stale backend. Refusing to run rather than",
      "  produce failures that mean something else.",
      "",
      RESTART,
      "",
    ].join("\n"),
  );
}
