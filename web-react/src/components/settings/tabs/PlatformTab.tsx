import { Link, useOutletContext } from "react-router";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { Section } from "../fields/Section";

const DASH = "—";

function yesNo(v: unknown): string {
  return v ? "Yes" : "No";
}
function orDash(v: unknown): string {
  return v === undefined || v === null || v === "" ? DASH : String(v);
}

// Read-only by design: every value below is owned by the Setup Wizard, which
// writes them on finish (and DERIVES dc_fan for x86_numato / ft232h_relay).
// Editing them here would be silently overwritten by the next wizard run, so
// this tab summarises and links out instead.
export function PlatformTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const platform = settings.platform ?? {};
  const outputs = platform.outputs ?? {};

  const summary: { label: string; value: string }[] = [
    { label: "Board / Profile", value: orDash(platform.current) },
    { label: "System Type", value: orDash(platform.system_type) },
    { label: "Fan Type", value: platform.dc_fan ? "DC Fan (PWM)" : "AC Fan" },
    { label: "Relay Trigger Level", value: orDash(platform.triggerlevel) },
    { label: "Standalone", value: yesNo(platform.standalone) },
    { label: "Real Hardware", value: yesNo(platform.real_hw) },
  ];
  const pins: { label: string; value: string }[] = [
    { label: "Auger", value: orDash(outputs.auger) },
    { label: "Fan", value: orDash(outputs.fan) },
    { label: "Igniter", value: orDash(outputs.igniter) },
    { label: "Power", value: orDash(outputs.power) },
    { label: "DC Fan", value: orDash(outputs.dc_fan) },
    { label: "PWM", value: orDash(outputs.pwm) },
  ];

  return (
    <div className="pf-settings-tab" data-tab="platform">
      <Section title="Grill Platform">
        <p className="pf-section-note">
          Platform hardware is configured by the Setup Wizard. These values are shown here for
          reference only.
        </p>
        <Link className="pf-btn" to="/wizard">
          Configure in Setup Wizard
        </Link>
        <dl className="pf-kv">
          {summary.map((row) => (
            <div className="pf-kv-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <Section title="Output Pins">
        <dl className="pf-kv">
          {pins.map((row) => (
            <div className="pf-kv-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}
