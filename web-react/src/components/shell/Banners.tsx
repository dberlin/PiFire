import { useState } from "react";
import { dismissWarnings } from "../../helpers/shell/warningsApi";
import "./shell.css";

// Errors/warnings/critical strip. Source: dash.errors / dash.warnings /
// dash.criticalError (socket_dash_data).
//
// Part of the shell rather than the dashboard because Flask renders these
// alerts on every page (templates/base.html), not only on the controller view.
//
// Warnings are dismissable and errors are not: Flask cleared warnings when a
// human rendered the dashboard, while errors clear only when the devices are
// rebuilt. Dismissal is keyed on warningsMaxId, so warnings raised after the
// dismissed payload reappear instead of being hidden by a stale click.
export function Banners({
  errors,
  warnings,
  warningsMaxId,
  criticalError,
}: {
  errors: string[];
  warnings: string[];
  warningsMaxId: number | null;
  criticalError: boolean;
}) {
  // Ids start at 1, so 0 means "nothing dismissed yet".
  const [dismissedThroughId, setDismissedThroughId] = useState(0);
  const errorLevel: "critical" | "error" = criticalError ? "critical" : "error";
  const showWarnings = warningsMaxId !== null && warningsMaxId > dismissedThroughId;
  const items: { t: string; level: "critical" | "error" | "warning" }[] = [
    ...errors.map((t) => ({ t, level: errorLevel })),
    ...(showWarnings ? warnings.map((t) => ({ t, level: "warning" as const })) : []),
  ];
  if (items.length === 0) return null;

  const onDismiss = async () => {
    if (warningsMaxId === null) return;
    // Only record the dismissal once the server confirms; otherwise the banner
    // stays up and the user can try again.
    if (await dismissWarnings(warningsMaxId)) setDismissedThroughId(warningsMaxId);
  };

  return (
    <div className="pf-banners">
      {items.map((it, i) => (
        <div key={i} className={`pf-banner pf-banner--${it.level}`}>
          {it.t}
        </div>
      ))}
      {showWarnings ? (
        <button
          type="button"
          className="pf-banner-dismiss"
          onClick={onDismiss}
          aria-label="Dismiss warnings"
        >
          Dismiss warnings
        </button>
      ) : null}
    </div>
  );
}
