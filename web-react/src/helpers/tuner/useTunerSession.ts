import { useCallback, useEffect, useRef, useState } from "react";

import { closeSession, openSession, tunerErrorText } from "./tunerApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export type SessionStatus = "idle" | "opening" | "open" | "refused" | "failed";

/**
 * Owns the lifetime of a tuning session.
 *
 * A session moves the operator's grill into Monitor. The contract this hook
 * exists to keep is that one is NEVER left open: it closes on unmount, and it
 * closes even when the unmount races an open that is still in flight -- that
 * case would otherwise land the open on a page that no longer exists, leaving
 * the grill in Monitor with nothing on screen to say so.
 *
 * Mounting deliberately does NOT open. Navigating to /tuner to read the
 * instructions is not consent to switch the grill's mode; `start` is.
 */
export function useTunerSession(baseUrl = BASE_URL) {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  //  Refs, not state: the unmount cleanup below reads these AFTER the last
  //  render, so a state value would be the one captured when the effect was
  //  created rather than the current one.
  const opened = useRef(false);
  const mounted = useRef(true);

  const close = useCallback(() => {
    if (!opened.current) return;
    opened.current = false;
    void closeSession(baseUrl);
  }, [baseUrl]);

  const start = useCallback(() => {
    setStatus("opening");
    setError(null);
    openSession(baseUrl).then((result) => {
      if (result.ok) {
        opened.current = true;
        //  The unmount may already have happened while this was in flight. The
        //  session is open on the server regardless, so close it rather than
        //  returning early and orphaning it.
        if (!mounted.current) {
          close();
          return;
        }
        setStatus("open");
        return;
      }
      if (!mounted.current) return;
      setStatus(result.status === 409 ? "refused" : "failed");
      setError(tunerErrorText(result));
    });
  }, [baseUrl, close]);

  const stop = useCallback(() => {
    close();
    setStatus("idle");
    setError(null);
  }, [close]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      close();
    };
  }, [close]);

  return { status, error, start, stop };
}
