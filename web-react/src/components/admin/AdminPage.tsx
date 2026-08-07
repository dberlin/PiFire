import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { adminErrorText, fetchAdminState } from "../../helpers/admin/adminApi";
import type { AdminResult, AdminState, Reading } from "../../helpers/admin/adminTypes";
import { queryKeys } from "../../helpers/query/keys";
import { ApiError, unwrap } from "../../helpers/query/unwrap";
import "./admin.css";
import { BackupsCard } from "./BackupsCard";
import { LogsCard } from "./LogsCard";
import { MaintenanceCard } from "./MaintenanceCard";
import { SystemCard } from "./SystemCard";
import { SystemUpdateCard } from "./SystemUpdateCard";

// Same-origin, matching every other module. Deliberately NOT `targetUrl` from
// the shell context: that value is absolute so ConnectionStatus has something
// readable to show, and fetching with it sends every request cross-origin,
// which Flask answers without CORS headers.
const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Human copy for a control mode. The server reports the raw mode string. */
function modeLabel(mode: string): string {
  return mode || "Unknown";
}

/** Bytes as the kernel reports them, in the units it counts in.
 *
 * 1024-based and labelled GiB rather than dividing by 1024 and calling the
 * result GB, which is the common way to be wrong by 7%. "Unknown" passes
 * straight through -- the fallback substitutes that string for a reading the
 * platform could not take. */
function formatBytes(value: Reading): string {
  if (typeof value !== "number") return String(value);
  const gib = value / 1024 ** 3;
  return gib >= 1 ? `${gib.toFixed(1)} GiB` : `${(value / 1024 ** 2).toFixed(0)} MiB`;
}

/** MHz, rounded. The live probe reports a float with fifteen decimals of
 * precision it does not have. */
function formatFrequency(value: Reading): string {
  return typeof value === "number" ? `${Math.round(value)} MHz` : String(value);
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
 *
 * `os_info` is /etc/os-release as a MAP, not a display string. Rendering it
 * directly crashed React, and no unit test caught it, because the fixture had
 * been written from the same wrong assumption as the type. tests/e2e's
 * admin.spec.ts reads the live endpoint, which is what found it.
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
          <Fact label="OS" value={system.os_info.PRETTY_NAME} />
          <Fact
            label="Architecture"
            value={`${system.os_info.ARCHITECTURE} (${system.os_info.BITS})`}
          />
          <Fact label="CPU" value={String(cpu.model_name)} />
          <Fact label="CPU Model" value={String(cpu.model)} />
          <Fact label="Cores" value={String(cpu.cores)} />
          <Fact label="Frequency" value={formatFrequency(cpu.frequency)} />
          <Fact label="Hardware" value={String(cpu.hardware)} />
          <Fact label="Total RAM" value={formatBytes(system.hardware_info.total_ram)} />
          <Fact label="Available RAM" value={formatBytes(system.hardware_info.available_ram)} />
          {interfaces.map(([name, iface]) => (
            <Fact key={name} label={name} value={`${iface.ip_address} · ${iface.mac_address}`} />
          ))}
        </dl>
      </div>
    </section>
  );
}

/**
 * Recover adminErrorText's human copy from a failed read's query error.
 *
 * Rebuilds the envelope unwrap() took apart: `field` and `mode` ride on the
 * ApiError (helpers/query/unwrap.ts) precisely so a `not_stopped` /
 * `bad_request` refusal keeps the detail adminErrorText branches on, instead
 * of falling back to "...in another mode." / "The server refused that
 * request." Not reachable from THIS read today --
 * blueprints/api_admin/routes.py's `/state` route is not mode-gated, only the
 * destructive actions are -- but adminApi.ts's unpack() populates both on
 * every call, so the day /state grows a gate the copy follows.
 */
function queryErrorText(error: unknown): string {
  const result: AdminResult<unknown> =
    error instanceof ApiError
      ? {
          ok: false,
          status: error.status,
          message: error.message,
          field: error.field,
          mode: error.mode,
          data: null,
        }
      : {
          ok: false,
          status: 0,
          message: error instanceof Error ? error.message : "Request failed",
          data: null,
        };
  return adminErrorText(result);
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
  const queryClient = useQueryClient();

  const {
    data: state,
    isPending,
    error,
  } = useQuery({
    queryKey: queryKeys.adminState,
    queryFn: () => unwrap(fetchAdminState(BASE_URL)),
    //  Both explicit, not inherited. adminApi.ts:99 -- state_payload() calls
    //  gather_system_info(), which probes the platform and writes the readings
    //  back into control. This is a read with side effects: on mount and after
    //  a change, never on a timer, and never off an unrelated network blip --
    //  the query client's defaults (helpers/query/queryClient.ts) leave
    //  refetchOnReconnect at react-query's default (refetch-if-stale), which
    //  would otherwise re-probe the platform the moment a Wi-Fi appliance's
    //  link drops and comes back, with no action from whoever has this page
    //  open.
    refetchInterval: false,
    refetchOnReconnect: false,
  });

  /** What every card -- and the Refresh button -- calls once its write lands. */
  const reload = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.adminState }),
    [queryClient],
  );

  if (isPending) {
    return (
      <div className="pf-admin pf-admin-empty">
        <p>Loading system information…</p>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="pf-admin pf-admin-empty">
        <p role="alert">{error ? queryErrorText(error) : "The server did not answer."}</p>
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
            {queryErrorText(error)}
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
      <SystemUpdateCard />
      <MaintenanceCard settings={state.settings} onChanged={reload} />
      <BackupsCard backups={state.backups} mode={state.mode} onChanged={reload} />
      <LogsCard logs={state.logs} onChanged={reload} />
    </div>
  );
}
