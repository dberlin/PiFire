import { useEffect, useRef } from "react";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// Slow on purpose. An update is a rare event and the answer is a few dozen
// bytes, but this runs for as long as a tab is open -- on a grill that can be
// all day.
const POLL_MS = 60_000;

/** The build the server is serving, or null when it cannot be determined
 *  (no bundle on disk, backend restarting, network down). */
export async function fetchBuildId(baseUrl = BASE_URL): Promise<string | null> {
  try {
    const response = await fetch(`${baseUrl}/api/webui`, { cache: "no-store" });
    if (!response.ok) return null;
    const body = (await response.json()) as { build?: string | null };
    return body.build ?? null;
  } catch {
    // Mid-update the backend is restarting and this will fail for a while.
    // That is not a new build; it is no answer at all.
    return null;
  }
}

/**
 * Reload the page once the server starts serving a different build.
 *
 * The updater rebuilds web-react/dist in place, so a tab left open across an
 * update goes on running the previous bundle against a backend that has moved.
 * This picks the new one up without the user reloading anything.
 *
 * Only a CHANGED id reloads. A null -- backend down, mid-restart, no bundle
 * built -- is never a change, so a grill that briefly loses its backend does
 * not reload itself in a loop.
 */
export function useWebUiBuild(baseUrl = BASE_URL, reload = () => window.location.reload()) {
  // The build this tab is running. Captured from the first successful read
  // rather than baked into the bundle, so it needs no build-time plumbing.
  const runningRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      const serving = await fetchBuildId(baseUrl);
      if (cancelled || serving === null) return;
      if (runningRef.current === null) {
        runningRef.current = serving;
        return;
      }
      if (serving !== runningRef.current) reload();
    };

    void check();
    const id = window.setInterval(() => void check(), POLL_MS);
    // A tab left open on a phone is suspended, not polling; coming back to it
    // is the moment a stale bundle is most likely and most worth catching.
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [baseUrl, reload]);
}
