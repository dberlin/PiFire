import "./shell.css";

// Errors/warnings/critical strip. Source: dash.errors / dash.warnings /
// dash.criticalError (socket_dash_data).
//
// Part of the shell rather than the dashboard because Flask renders these
// alerts on every page (templates/base.html), not only on the controller view.
export function Banners({
  errors,
  warnings,
  criticalError,
}: {
  errors: string[];
  warnings: string[];
  criticalError: boolean;
}) {
  const errorLevel: "critical" | "error" = criticalError ? "critical" : "error";
  const items: { t: string; level: "critical" | "error" | "warning" }[] = [
    ...errors.map((t) => ({ t, level: errorLevel })),
    ...warnings.map((t) => ({ t, level: "warning" as const })),
  ];
  if (items.length === 0) return null;
  return (
    <div className="pf-banners">
      {items.map((it, i) => (
        <div key={i} className={`pf-banner pf-banner--${it.level}`}>
          {it.t}
        </div>
      ))}
    </div>
  );
}
