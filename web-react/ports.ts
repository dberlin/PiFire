/// <reference types="node" />
/**
 * The one place any port or origin is decided.
 *
 * Several checkouts of this repo (jj workspaces) are worked on at the same
 * time, and each needs its own dev servers and its own PiFire backend. When
 * these values were literals, two workspaces both served :5173 and Playwright's
 * `reuseExistingServer` quietly attached to whichever started first -- so a
 * suite would pass or fail against a tree nobody was looking at. Nothing here
 * may be hardcoded anywhere else; import from this module instead.
 *
 * Defaults reproduce the single-checkout setup exactly, so the main checkout
 * needs no environment at all. To run a second one, give it three values:
 *
 *     PORT=5273 DEMO_PORT=5274 PUBLIC_PIFIRE_URL=http://localhost:5100
 *
 * `PUBLIC_PIFIRE_URL` is also what rsbuild proxies /api and /socket.io to, and
 * it is read by the browser bundle, so it must name the backend that workspace
 * runs -- one started with a matching `PIFIRE_DB_PATH`, or the two checkouts
 * share a datastore and race over grill mode.
 */

export type PortEnv = {
  PORT?: string;
  DEMO_PORT?: string;
  PUBLIC_PIFIRE_URL?: string;
};

export type Ports = {
  /** rsbuild dev server for the app, proxying to the backend. */
  appPort: number;
  /** A second dev server built with PUBLIC_DEMO=1. Demo mode is a BUILD-time
   *  flag (helpers/useLiveState.ts), which is why it cannot be a query
   *  parameter on the first server. */
  demoPort: number;
  appUrl: string;
  demoUrl: string;
  /** Origin of the PiFire Flask/gunicorn backend. */
  pifireUrl: string;
};

const DEFAULT_APP_PORT = 5173;
const DEFAULT_DEMO_PORT = 5174;
const DEFAULT_PIFIRE_URL = "http://localhost:5000";

/** Pure so it can be tested without mutating the real environment. */
export function resolvePorts(env: PortEnv = {}): Ports {
  // Number("") is 0 and Number(undefined) is NaN; both are falsy here, so an
  // unset or empty variable falls back rather than binding to port 0.
  const appPort = Number(env.PORT) || DEFAULT_APP_PORT;
  const demoPort = Number(env.DEMO_PORT) || DEFAULT_DEMO_PORT;
  return {
    appPort,
    demoPort,
    appUrl: `http://localhost:${appPort}`,
    demoUrl: `http://localhost:${demoPort}`,
    // Trailing slashes would double up when callers append "/api/...".
    pifireUrl: (env.PUBLIC_PIFIRE_URL || DEFAULT_PIFIRE_URL).replace(/\/+$/, ""),
  };
}

export const ports = resolvePorts(process.env as PortEnv);
