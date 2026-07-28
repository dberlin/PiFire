import { useCallback, useEffect, useState } from "react";
import { adminErrorText, fetchAdminState } from "../../helpers/admin/adminApi";
import type { AdminResult, AdminState } from "../../helpers/admin/adminTypes";
import "./admin.css";
import { MaintenanceCard } from "./MaintenanceCard";
import { SystemCard } from "./SystemCard";

// Same-origin, matching every other module. Deliberately NOT `targetUrl` from
// the shell context: that value is absolute so ConnectionStatus has something
// readable to show, and fetching with it sends every request cross-origin,
// which Flask answers without CORS headers.
const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Human copy for a control mode. The server reports the raw mode string. */
function modeLabel(mode: string): string {
  return mode || "Unknown";
}

/** One label/value row of the system panel. */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="pf-admin-fact">
      <dt className="pf-admin-fact-label">{label}</dt>
      <dd className="pf-admin-fact-value">{value}</dd>
    </div>
  );
}

/**
 * The machine's own readings, from gather_system_info().
 *
 * Every field falls back to the literal string "Unknown" server-side when the
 * platform could not answer, so nothing here is conditional -- an unprobed
 * reading shows as Unknown rather than vanishing, which is what the Flask page
 * did and is the honest answer to "we asked and got nothing".
 */
function SystemInfo({ state }: { state: AdminState }) {
  const { system } = state;
  const cpu = system.hardware_info.cpu_info;
  const interfaces = Object.entries(system.network_info);

  return (
    // The heading id deliberately carries no `pf-` prefix: cssCoverage's
    // classesUsedIn() scans source strings for `pf-*` and would take one for a
    // class with no rule behind it.
    <section className="pf-admin-card pf-admin-wide" aria-labelledby="admin-system-info">
      <h2 className="pf-admin-card-title" id="admin-system-info">
        System
      </h2>
      <div className="pf-admin-scroll">
        <dl className="pf-admin-facts">
          {/* uptime(1) output arrives with its trailing newline attached. */}
          <Fact label="Uptime" value={system.uptime.trim()} />
          <Fact label="OS" value={system.os_info} />
          <Fact label="Model" value={cpu.model} />
          <Fact label="CPU" value={cpu.model_name} />
          <Fact label="Cores" value={cpu.cores} />
          <Fact label="Frequency" value={cpu.frequency} />
          <Fact label="Hardware" value={cpu.hardware} />
          <Fact label="Total RAM" value={system.hardware_info.total_ram} />
          <Fact label="Available RAM" value={system.hardware_info.available_ram} />
          {interfaces.map(([name, iface]) => (
            <Fact key={name} label={name} value={`${iface.ip_address} · ${iface.mac_address}`} />
          ))}
        </dl>
      </div>
    </section>
  );
}

/**
 * The admin page: system readings, the destructive actions, maintenance
 * clears, backups and logs.
 *
 * ONE read builds the whole page. GET /api/admin/state is not free -- it calls
 * gather_system_info(), which probes the platform and writes the readings back
 * into control -- so it runs on mount and after a change, never on a timer.
 * Every card takes `onChanged` and calls it once its write lands, which is why
 * there is no per-card fetch anywhere below.
 *
 * `state.mode` rides in that same payload rather than coming off the live
 * socket: the destructive controls are disabled unless the grill is stopped,
 * and a mode read separately from the backups list could disagree with the
 * server that will judge the request.
 */
export function AdminPage() {
  const [state, setState] = useState<AdminState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const apply = useCallback((result: AdminResult<AdminState>) => {
    if (result.ok && result.data) {
      setState(result.data);
      setError(null);
    } else {
      setError(adminErrorText(result));
    }
    setLoading(false);
  }, []);

  /** What every card calls once its write lands. */
  const reload = useCallback(() => fetchAdminState(BASE_URL).then(apply), [apply]);

  useEffect(() => {
    //  The `cancelled` latch matters here rather than being ceremony: this
    //  request probes hardware, so it is one of the slowest on the site, and a
    //  user who navigates away mid-probe would otherwise land a setState on an
    //  unmounted tree.
    let cancelled = false;
    fetchAdminState(BASE_URL).then((result) => {
      if (!cancelled) apply(result);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  if (loading) {
    return (
      <div className="pf-admin pf-admin-empty">
        <p>Loading system information…</p>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="pf-admin pf-admin-empty">
        <p role="alert">{error ?? "The server did not answer."}</p>
      </div>
    );
  }

  return (
    <div className="pf-admin">
      {/* Always rendered, empty or not: grid auto-placement would shift every
          card down a row the moment an action was rejected. */}
      <div className="pf-admin-error">
        {error && (
          <p className="pf-settings-error-text" role="alert">
            {error}
          </p>
        )}
      </div>

      <header className="pf-admin-header">
        <h1 className="pf-admin-title">Admin</h1>
        <span className="pf-admin-mode">{`Grill mode: ${modeLabel(state.mode)}`}</span>
        {/* The readings below are a point-in-time probe, not a subscription --
            nothing pushes a new CPU temperature or uptime -- so the only way to
            see a current one is to ask again. This is the same refetch every
            card runs after a write. */}
        <button type="button" className="pf-admin-btn" onClick={() => void reload()}>
          Refresh
        </button>
      </header>

      <SystemInfo state={state} />
      <SystemCard mode={state.mode} />
      <MaintenanceCard settings={state.settings} onChanged={reload} />
    </div>
  );
}
