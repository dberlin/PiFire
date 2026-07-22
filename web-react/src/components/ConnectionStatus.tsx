import type { ConnectionPhase } from "../useDashData";

// Shown instead of the dashboard while we have no live data in `dev` mode.
// Names the URL being contacted and whether it's reachable — never fakes data.
export function ConnectionStatus({
  phase,
  targetUrl,
}: {
  phase: ConnectionPhase;
  targetUrl: string;
}) {
  const unreachable = phase === "unreachable";
  return (
    <div className="conn">
      <div className={`conn-dot ${unreachable ? "err" : "wait"}`} />
      <h1 className="conn-title">
        {unreachable ? "PiFire not reachable" : "Connecting to PiFire…"}
      </h1>
      <p className="conn-url">
        {unreachable ? "Tried" : "Contacting"} <code>{targetUrl}</code>
      </p>
      {unreachable && (
        <p className="conn-hint">
          Start PiFire (its web app must be up), or point the dev server at it with{" "}
          <code>VITE_PIFIRE_URL=http://&lt;host&gt;:5000 bun run dev</code>. Retrying automatically…
          <br />
          No hardware? Run the offline demo: <code>bun run demo</code>.
        </p>
      )}
    </div>
  );
}
