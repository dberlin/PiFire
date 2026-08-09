import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  activateModel,
  fetchModelEvidenceReport,
  rollbackModel,
  setMpcCalibration,
} from "../../helpers/modelEvidence/modelEvidenceApi";
import type {
  ModelEvidenceReport,
  ModelEvidenceStatus,
  ModelIdentity,
  ModelScore,
  MpcCalibrationAction,
  TemperatureUnit,
} from "../../helpers/modelEvidence/types";

const REPORT_REFRESH_MS = 5_000;
const BAND_CENTERS_F = [225, 325, 425];
const FOCUS_RING =
  "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent";
const REPORT_SECTION = "min-w-0 rounded-card border border-card-border bg-inset p-4";

function bandCenters(units: TemperatureUnit): string {
  const values = BAND_CENTERS_F.map((f) => (units === "F" ? f : Math.round(((f - 32) * 5) / 9)));
  return `${values[0]}, ${values[1]} and ${values[2]} °${units}`;
}

const STATUS_LABEL: Record<ModelEvidenceStatus, string> = {
  collecting: "Collecting",
  "insufficient-excitation": "Insufficient excitation",
  fitting: "Fitting",
  evaluating: "Evaluating",
  "ready-for-review": "Ready for review",
  activating: "Activating",
  active: "Active",
  fallback: "Fallback",
  error: "Error",
  "schema-invalidated": "Schema invalidated",
};

const STATUS_TONE: Record<ModelEvidenceStatus, string> = {
  collecting: "text-probe-label",
  "insufficient-excitation": "text-warn",
  fitting: "text-accent",
  evaluating: "text-accent",
  "ready-for-review": "text-ok",
  activating: "text-accent",
  active: "text-ok",
  fallback: "text-warn",
  error: "text-danger",
  "schema-invalidated": "text-danger",
};

interface MpcLearningPanelProps {
  apiBase: string;
  selectedController: string | null;
  units: TemperatureUnit;
  ambientC: number;
  /** Socket high-water mark. It invalidates REST authority but is never rendered. */
  learningReportRevision?: number;
}

function metric(value: number | null, suffix = ""): string {
  return value === null ? "Unavailable" : `${value.toFixed(2)}${suffix}`;
}

function value(value: number | null, unit = ""): string {
  return value === null ? "Unavailable" : `${value}${unit ? ` ${unit}` : ""}`;
}

function identitySummary(label: string, identity: ModelIdentity | null) {
  if (identity === null) return <p>{label}: none</p>;
  return (
    <div className="grid gap-1">
      <p>
        {label}: {identity.kind}
      </p>
      <p className="break-all text-probe-label">{identity.digest ?? "Digest unavailable"}</p>
      <p className="text-probe-label">
        Schema {identity.model_schema ?? "unavailable"} · role generation {identity.role_generation ?? "unavailable"} · candidate generation {identity.candidate_generation ?? "unavailable"}
      </p>
    </div>
  );
}

function ScoreDetails({ score }: { score: ModelScore }) {
  return (
    <>
      <div>
        <dt className="text-label">Horizon</dt>
        <dd>{score.horizon_steps} steps</dd>
      </div>
      <div>
        <dt className="text-label">Band / phase</dt>
        <dd>
          {score.temperature_band} / {score.phase}
        </dd>
      </div>
      <div>
        <dt className="text-label">Challenger RMSE</dt>
        <dd>{metric(score.challenger_rmse_c, " °C")}</dd>
      </div>
      <div>
        <dt className="text-label">Incumbent RMSE</dt>
        <dd>{metric(score.incumbent_rmse_c, " °C")}</dd>
      </div>
      <div>
        <dt className="text-label">Challenger bias / band error</dt>
        <dd>
          {metric(score.challenger_bias_c, " °C")} / {metric(score.challenger_band_error_c, " °C")}
        </dd>
      </div>
      <div>
        <dt className="text-label">Incumbent bias / band error</dt>
        <dd>
          {metric(score.incumbent_bias_c, " °C")} / {metric(score.incumbent_band_error_c, " °C")}
        </dd>
      </div>
      <div>
        <dt className="text-label">95% ratio upper bound</dt>
        <dd>{metric(score.bootstrap.rmse_ratio_upper_bound)}</dd>
      </div>
    </>
  );
}

export function MpcLearningPanel(props: MpcLearningPanelProps) {
  return props.selectedController === "mpc" ? <ActiveMpcLearningPanel {...props} /> : null;
}

function ActiveMpcLearningPanel({
  apiBase,
  units,
  ambientC,
  learningReportRevision,
}: MpcLearningPanelProps) {
  const [open, setOpen] = useState(false);
  // This is the only report object in the component. Pill and dialog both project it directly.
  const [report, setReport] = useState<ModelEvidenceReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportError, setReportError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [emptyGrill, setEmptyGrill] = useState(false);
  const [pellets, setPellets] = useState(false);
  const [pendingActions, setPendingActions] = useState<Set<MpcCalibrationAction>>(new Set());
  const [candidateDigestConfirmation, setCandidateDigestConfirmation] = useState("");
  const [decisionIdConfirmation, setDecisionIdConfirmation] = useState("");
  const [activationPending, setActivationPending] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackPending, setRollbackPending] = useState(false);
  const requestGeneration = useRef(0);
  const nextRevision = useRef(0);
  const lastLearningReportRevision = useRef(learningReportRevision);
  const triggerButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);
  const confirmationIdentity = useRef<{ candidateDigest: string | null; decisionId: string | null }>({
    candidateDigest: null,
    decisionId: null,
  });

  const refreshReport = useCallback(async () => {
    const generation = ++requestGeneration.current;
    const result = await fetchModelEvidenceReport(apiBase);
    if (generation !== requestGeneration.current) return;
    setLoading(false);
    if (!result.ok || result.data === null) {
      // Keep any prior report for inspection, but never let stale success hide this failure.
      setReportError(result.message || "Model evidence report unavailable");
      return;
    }
    setReport(result.data);
    setReportError(null);
  }, [apiBase]);

  const loadReport = useCallback(async () => {
    setLoading(true);
    await refreshReport();
  }, [refreshReport]);

  useEffect(() => {
    const initialRefresh = window.setTimeout(() => void refreshReport(), 0);
    const interval = window.setInterval(() => void refreshReport(), REPORT_REFRESH_MS);
    return () => {
      requestGeneration.current += 1;
      window.clearTimeout(initialRefresh);
      window.clearInterval(interval);
    };
  }, [refreshReport]);

  useEffect(() => {
    if (lastLearningReportRevision.current === learningReportRevision) return;
    lastLearningReportRevision.current = learningReportRevision;
    void refreshReport();
  }, [learningReportRevision, refreshReport]);

  useEffect(() => {
    if (!open) {
      if (wasOpen.current) triggerButton.current?.focus();
      wasOpen.current = false;
      return;
    }
    wasOpen.current = true;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = dialog.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const focusIsInsideDialog =
        document.activeElement !== null &&
        dialog.current?.contains(document.activeElement) === true;
      if (!focusIsInsideDialog) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    const candidateDigest = report?.candidate.digest ?? null;
    const decisionId = report?.decision_id ?? null;
    const previous = confirmationIdentity.current;
    if (previous.candidateDigest === candidateDigest && previous.decisionId === decisionId) return;
    confirmationIdentity.current = { candidateDigest, decisionId };
    setCandidateDigestConfirmation("");
    setDecisionIdConfirmation("");
  }, [report?.candidate.digest, report?.decision_id]);

  const calibrationRunning =
    report?.calibration.status === "active" || report?.calibration.status === "running";
  const calibrationPaused = report?.calibration.status === "paused";
  const calibrationEnded =
    report !== null &&
    !calibrationRunning &&
    !calibrationPaused &&
    report.calibration.status !== "inactive" &&
    report.calibration.status !== "idle";
  const resetProgressAvailable =
    report !== null &&
    ["cancelled", "timed-out", "failed", "completed"].includes(report.calibration.status);
  const startDisabled =
    report === null ||
    calibrationRunning ||
    calibrationPaused ||
    !emptyGrill ||
    !pellets ||
    pendingActions.has("start");
  const activationReady =
    report?.status === "ready-for-review" &&
    report.activation.policy === "operator-reviewed" &&
    report.candidate.digest !== null &&
    report.decision_id !== null;
  const activationConfirmed =
    activationReady &&
    candidateDigestConfirmation === report.candidate.digest &&
    decisionIdConfirmation === report.decision_id;
  const rollbackAvailable =
    report !== null && report.rollback.permitted && report.rollback_owner !== null;

  const runAction = async (action: MpcCalibrationAction) => {
    if (pendingActions.has(action)) return;
    const revision = Math.max(nextRevision.current, report?.calibration.revision ?? 0) + 1;
    nextRevision.current = revision;
    setPendingActions((current) => new Set(current).add(action));
    setActionError(null);
    const isStart = action === "start";
    const result = await setMpcCalibration(
      {
        action,
        revision,
        ambient_c: ambientC,
        ambient_source: "configured",
        empty_grill_confirmed: isStart ? emptyGrill : true,
        pellets_confirmed: isStart ? pellets : true,
      },
      apiBase,
    );
    if (!result.ok) setActionError(result.message || `${action} was not accepted`);
    // The command response is only an acknowledgement. Keep this revision
    // pending until the report authority has settled so stale controls cannot
    // submit it again.
    await loadReport();
    setPendingActions((current) => {
      const next = new Set(current);
      next.delete(action);
      return next;
    });
  };

  const runActivation = async () => {
    if (!activationConfirmed || activationPending || report === null) return;
    setActivationPending(true);
    setActionError(null);
    const result = await activateModel(
      {
        candidate_digest: report.candidate.digest!,
        decision_id: report.decision_id!,
      },
      apiBase,
    );
    if (!result.ok || result.data?.accepted === false) {
      setActionError(
        result.data?.detail || result.message || "Model activation was not accepted",
      );
    } else {
      setCandidateDigestConfirmation("");
      setDecisionIdConfirmation("");
    }
    // Never display activation success from the acknowledgement. Keep the
    // control pending until the authoritative report has settled.
    await loadReport();
    setActivationPending(false);
  };

  const runRollback = async () => {
    const reason = rollbackReason.trim();
    if (reason === "" || rollbackPending || !rollbackAvailable) return;
    setRollbackPending(true);
    setActionError(null);
    const result = await rollbackModel({ reason }, apiBase);
    if (!result.ok || result.data?.accepted === false) {
      setActionError(result.data?.detail || result.message || "Model rollback was not accepted");
    } else {
      setRollbackReason("");
    }
    // Never infer ownership or fallback from the acknowledgement. Keep the
    // control pending until the authoritative report has settled.
    await loadReport();
    setRollbackPending(false);
  };

  const triggerStatus =
    reportError !== null
      ? "error"
      : loading && report === null
        ? "loading"
        : report
          ? STATUS_LABEL[report.status].toLowerCase()
          : "unavailable";

  return (
    <>
      <button
        ref={triggerButton}
        className={`pf-btn pf-dash-mpc-learning ${FOCUS_RING}`}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-busy={(loading && report === null) || undefined}
        onClick={() => setOpen(true)}
      >
        MPC learning: {triggerStatus}
      </button>
      {open &&
        createPortal(
          <div className="pf-modal-scrim pf-modal-scrim-fixed" onClick={() => setOpen(false)}>
            <section
              ref={dialog}
              className="pf-modal max-h-full w-11/12 max-w-5xl min-w-0 overflow-y-auto text-text"
              role="dialog"
              aria-modal="true"
              aria-labelledby="mpc-learning-title"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h2 id="mpc-learning-title" className="text-xl font-bold">
                    MPC model learning
                  </h2>
                  {report !== null && (
                    <p
                      className={`mt-1 font-semibold ${
                        reportError === null ? STATUS_TONE[report.status] : "text-danger"
                      }`}
                    >
                      {reportError === null ? STATUS_LABEL[report.status] : "Error"}
                    </p>
                  )}
                </div>
                <button
                  ref={closeButton}
                  className={`pf-toggle shrink-0 ${FOCUS_RING}`}
                  type="button"
                  aria-label="Close MPC model learning"
                  onClick={() => setOpen(false)}
                >
                  Close
                </button>
              </header>

              {loading && report === null && <p role="status">Loading model evidence…</p>}
              {reportError !== null && (
                <div className="rounded-lg border border-danger p-3 text-danger" role="alert">
                  <p>{reportError}</p>
                  <button
                    className={`pf-modal-btn mt-2 ${FOCUS_RING}`}
                    type="button"
                    onClick={() => void loadReport()}
                  >
                    Retry evidence report
                  </button>
                </div>
              )}

              {report !== null && (
                <>
                  {report.errors.length > 0 && (
                    <div className="grid gap-2 rounded-lg border border-danger p-3 text-danger" role="alert">
                      {report.errors.map((error) => (
                        <p key={`${error.timestamp_ms}-${error.code}`}>
                          <strong>{error.code}</strong> — {error.message} ({error.phase})
                          {error.retryable ? " — retryable" : ""}
                        </p>
                      ))}
                    </div>
                  )}

                  <section className={REPORT_SECTION}>
                    <p className="text-sm text-probe-label">
                      Calibration probes around the hold you set; it does not drive the grill to a
                      temperature of its own. Hold at each of its three bands in turn (
                      {bandCenters(units)}), and it waits at the one it finished until you set the
                      next. Probes stay under the configured grill maximum.
                    </p>
                    <div className="mt-3 grid gap-2 text-sm">
                      <label className="flex items-start gap-2">
                        <input
                          className={`mt-1 accent-accent ${FOCUS_RING}`}
                          type="checkbox"
                          checked={emptyGrill}
                          onChange={(event) => setEmptyGrill(event.target.checked)}
                        />
                        The grill is empty, with normal grates and drip tray installed.
                      </label>
                      <label className="flex items-start gap-2">
                        <input
                          className={`mt-1 accent-accent ${FOCUS_RING}`}
                          type="checkbox"
                          checked={pellets}
                          onChange={(event) => setPellets(event.target.checked)}
                        />
                        Sufficient pellets are loaded for the calibration run.
                      </label>
                    </div>
                    <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-5">
                      <button
                        className={`pf-modal-btn accent ${FOCUS_RING}`}
                        type="button"
                        disabled={startDisabled}
                        aria-busy={pendingActions.has("start") || undefined}
                        onClick={() => void runAction("start")}
                      >
                        {pendingActions.has("start") ? "Start calibration…" : "Start calibration"}
                      </button>
                      {calibrationPaused ? (
                        <button
                          className={`pf-modal-btn ${FOCUS_RING}`}
                          type="button"
                          disabled={pendingActions.has("resume")}
                          aria-busy={pendingActions.has("resume") || undefined}
                          onClick={() => void runAction("resume")}
                        >
                          {pendingActions.has("resume") ? "Resume calibration…" : "Resume calibration"}
                        </button>
                      ) : (
                        <button
                          className={`pf-modal-btn ${FOCUS_RING}`}
                          type="button"
                          disabled={!calibrationRunning || pendingActions.has("pause")}
                          aria-busy={pendingActions.has("pause") || undefined}
                          onClick={() => void runAction("pause")}
                        >
                          {pendingActions.has("pause") ? "Pause calibration…" : "Pause calibration"}
                        </button>
                      )}
                      <button
                        className={`pf-modal-btn danger ${FOCUS_RING}`}
                        type="button"
                        disabled={pendingActions.has("stop")}
                        aria-busy={pendingActions.has("stop") || undefined}
                        onClick={() => void runAction("stop")}
                      >
                        {pendingActions.has("stop") ? "Stop calibration…" : "Stop calibration"}
                      </button>
                      {resetProgressAvailable && (
                        <button
                          className={`pf-modal-btn col-span-2 ${FOCUS_RING}`}
                          type="button"
                          disabled={pendingActions.has("reset-progress")}
                          aria-busy={pendingActions.has("reset-progress") || undefined}
                          onClick={() => void runAction("reset-progress")}
                        >
                          {pendingActions.has("reset-progress")
                            ? "Resetting calibration progress…"
                            : "Reset calibration progress"}
                        </button>
                      )}
                    </div>
                    {actionError !== null && (
                      <p className="mt-3 text-danger" role="alert">
                        {actionError}
                      </p>
                    )}
                  </section>

                  {(report.calibration.timed_out ||
                    (report.calibration.incomplete && calibrationEnded)) && (
                    <div className="rounded-lg border border-warn p-3 text-warn" role="alert">
                      {report.calibration.timed_out && <p>Calibration stage timed out.</p>}
                      {report.calibration.incomplete && calibrationEnded && (
                        <p>Calibration ended without completing.</p>
                      )}
                    </div>
                  )}

                  <div className="grid gap-4 md:grid-cols-2">
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Candidate and progress</h3>
                      <p className="mt-2">Role generation {report.role_generation}</p>
                      <p>Candidate generation {report.candidate_generation ?? "unavailable"}</p>
                      <p className="break-all text-sm text-probe-label">
                        {report.candidate.digest ?? "Candidate digest unavailable"}
                      </p>
                      <div className="mt-3 grid gap-1 border-t border-card-border pt-3 text-sm">
                        {identitySummary("Active model", report.active_model)}
                        {identitySummary("Default model", report.default_model)}
                      </div>
                      <div className="mt-3 grid gap-1 text-sm">
                        <p>Mode: {report.mode}</p>
                        <p>Origin: {report.origin}</p>
                        <p>Calibration: {report.calibration.status}</p>
                        <p>Stage: {report.calibration.stage ?? "not started"}</p>
                        <p>
                          Current probe: {report.calibration.current_probe === null
                            ? "none"
                            : `${report.calibration.current_probe >= 0 ? "+" : ""}${report.calibration.current_probe.toFixed(3)} q`}
                        </p>
                        <p>
                          {report.calibration.eligible_count} eligible / {report.calibration.ineligible_count} ineligible
                        </p>
                        <p>Completed stages: {report.calibration.completed_stages.join(", ") || "none"}</p>
                        <p>Missing stages: {report.calibration.missing_stages.join(", ") || "none"}</p>
                        {report.calibration.ineligible_reasons.length > 0 && (
                          <p>Ineligible reasons: {report.calibration.ineligible_reasons.join(", ")}</p>
                        )}
                      </div>
                    </section>

                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Observation eligibility</h3>
                      <div className="mt-2 grid gap-1 text-sm">
                        <p>Window: {report.observation.window_id ?? "unavailable"}</p>
                        <p>{report.observation.eligible_count} eligible</p>
                        <p>{report.observation.ineligible_count} ineligible</p>
                        <p>Probe provenance: {report.observation.probe_provenance}</p>
                        <p>
                          Mixed-window authority: {report.observation.mixed_window_authority ?? "none"}
                        </p>
                        {report.observation.rejection_reasons.length === 0 ? (
                          <p className="text-probe-label">No rejected observations.</p>
                        ) : (
                          <ul className="list-disc pl-5">
                            {report.observation.rejection_reasons.map((reason) => (
                              <li key={reason.reason}>{reason.reason}: {reason.count}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </section>
                  </div>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Fit job</h3>
                    <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
                      <p>Status: {report.fit.status}</p>
                      <p>Job: {report.fit.job_id ?? "none"}</p>
                      <p>Process: {report.fit.process_id ?? "none"}</p>
                      <p>Origin: {report.fit.origin}</p>
                      <p>Role generation: {report.fit.role_generation}</p>
                      {report.fit.window !== null && (
                        <>
                          <p>Window: {report.fit.window.window_id}</p>
                          <p>Session: {report.fit.window.session_id ?? "none"}</p>
                          <p>Cook: {report.fit.window.cook_id ?? "none"}</p>
                          <p>Samples: {report.fit.window.sample_count}</p>
                          <p className="break-all">Config digest: {report.fit.window.config_digest}</p>
                          <p className="break-all">Incumbent digest: {report.fit.window.incumbent_digest}</p>
                        </>
                      )}
                      {report.fit.result !== null && (
                        <>
                          <p>Result: {report.fit.result.reason}</p>
                          <p>Solver iterations: {report.fit.result.solver_iterations ?? "unavailable"}</p>
                        </>
                      )}
                    </div>
                  </section>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Grey parameter changes</h3>
                    <p className="mt-2 text-sm text-probe-label">
                      {report.candidate_structure.prediction_step_seconds}-second prediction step · {report.candidate_structure.delay_states} delay states · {report.candidate_structure.horizon_steps}-step horizon
                    </p>
                    {report.grey_parameters.length === 0 ? (
                      <p className="mt-2 text-probe-label">No candidate parameter changes yet.</p>
                    ) : (
                      <div className="mt-3 overflow-x-auto">
                        <table className="w-full text-left text-sm" aria-label="Grey parameter changes">
                          <thead className="text-label">
                            <tr>
                              <th className="p-2" scope="col">Parameter</th>
                              <th className="p-2" scope="col">Incumbent</th>
                              <th className="p-2" scope="col">Candidate</th>
                              <th className="p-2" scope="col">Delta</th>
                            </tr>
                          </thead>
                          <tbody>
                            {report.grey_parameters.map((parameter) => (
                              <tr className="border-t border-card-border" key={parameter.name}>
                                <th className="p-2" scope="row">{parameter.name}</th>
                                <td className="p-2">{value(parameter.incumbent_value, parameter.unit)}</td>
                                <td className="p-2">{value(parameter.candidate_value, parameter.unit)}</td>
                                <td className="p-2">{value(parameter.delta, parameter.unit)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </section>

                  <div className="grid gap-4 md:grid-cols-2">
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Native candidate</h3>
                      <div className="mt-2 grid gap-1 text-sm">
                        <p>Build: {report.native.build.status}</p>
                        <p className="break-all">Build digest: {report.native.build.build_digest ?? "unavailable"}</p>
                        <p className="break-all">Manifest digest: {report.native.build.manifest_digest ?? "unavailable"}</p>
                        <p>{report.native.build.detail ?? "No build detail."}</p>
                        <p>Dry solve: {report.native.dry_solve.status}</p>
                        <p>Solve time: {metric(report.native.dry_solve.solve_time_ms, " ms")}</p>
                        <p>Finite diagnostics: {report.native.dry_solve.finite_diagnostics ? "yes" : "no"}</p>
                        <p>{report.native.dry_solve.detail ?? "No dry-solve detail."}</p>
                      </div>
                    </section>

                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Readiness</h3>
                      <div className="mt-2 grid gap-1 text-sm">
                        <p>Identifiability: {report.identifiability.status}</p>
                        <p>{report.identifiability.reason ?? "No identifiability reason."}</p>
                        <p>Rank: {report.identifiability.matrix_rank ?? "unavailable"} / {report.identifiability.parameter_count}</p>
                        <p>Condition number: {report.identifiability.condition_number ?? "unavailable"}</p>
                        <p>Physical bounds: {report.identifiability.physical_bounds.status}</p>
                        <p>{report.identifiability.physical_bounds.detail ?? "No physical-bounds detail."}</p>
                        <p>Missing gates: {report.missing_gates.join(", ") || "none"}</p>
                        {report.gates.map((gate) => (
                          <p key={gate.name}>{gate.name}: {gate.status}{gate.reason ? ` — ${gate.reason}` : ""}</p>
                        ))}
                        {report.blockers.length === 0 ? (
                          <p className="text-ok">No current blockers.</p>
                        ) : (
                          <ul className="list-disc pl-5">
                            {report.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                          </ul>
                        )}
                      </div>
                    </section>
                  </div>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Activation and swap</h3>
                    <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
                      <p>Policy: {report.activation.policy}</p>
                      <p>Reason: {report.activation.reason}</p>
                      <p>Decision: {report.activation.decision_id ?? "none"}</p>
                      <p>Persistence check: {report.activation.persistence.status}</p>
                      <p>Persistence phase: {report.activation.persistence.phase ?? "none"}</p>
                      <p>Record: {report.activation.persistence.record_id ?? "none"}</p>
                      <p>{report.activation.persistence.detail ?? "No persistence detail."}</p>
                      <p>Pending swap: {report.activation.pending_swap.status}</p>
                      <p>Frame boundary: {report.activation.pending_swap.frame_boundary ?? "none"}</p>
                      <p>{report.activation.pending_swap.detail ?? "No swap detail."}</p>
                    </div>
                  </section>

                  {activationReady && (
                    <section className="min-w-0 rounded-card border border-ok bg-inset p-4">
                      <h3 className="font-bold">Activate reviewed model</h3>
                      <p className="mt-2 text-sm">
                        Confirm both exact values. The refreshed report, not this request acknowledgement, determines the activation state.
                      </p>
                      <dl className="mt-3 grid gap-2 text-sm">
                        <div>
                          <dt className="font-semibold">Candidate digest</dt>
                          <dd className="break-all text-probe-label">{report.candidate.digest}</dd>
                        </div>
                        <div>
                          <dt className="font-semibold">Confidence decision ID</dt>
                          <dd className="break-all text-probe-label">{report.decision_id}</dd>
                        </div>
                      </dl>
                      <div className="mt-3 grid gap-3 md:grid-cols-2">
                        <label className="grid gap-1 text-sm" htmlFor="mpc-activation-digest">
                          Type the exact candidate digest
                          <input
                            id="mpc-activation-digest"
                            className={`min-w-0 rounded-lg border border-card-border bg-card px-3 py-2 font-mono text-xs text-text ${FOCUS_RING}`}
                            autoComplete="off"
                            spellCheck={false}
                            value={candidateDigestConfirmation}
                            onChange={(event) => setCandidateDigestConfirmation(event.target.value)}
                          />
                        </label>
                        <label className="grid gap-1 text-sm" htmlFor="mpc-activation-decision">
                          Type the exact confidence decision ID
                          <input
                            id="mpc-activation-decision"
                            className={`min-w-0 rounded-lg border border-card-border bg-card px-3 py-2 font-mono text-xs text-text ${FOCUS_RING}`}
                            autoComplete="off"
                            spellCheck={false}
                            value={decisionIdConfirmation}
                            onChange={(event) => setDecisionIdConfirmation(event.target.value)}
                          />
                        </label>
                      </div>
                      <button
                        className={`pf-modal-btn accent mt-3 ${FOCUS_RING}`}
                        type="button"
                        disabled={!activationConfirmed || activationPending}
                        aria-busy={activationPending || undefined}
                        onClick={() => void runActivation()}
                      >
                        {activationPending ? "Activating exact model…" : "Activate exact model"}
                      </button>
                    </section>
                  )}

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Model ownership</h3>
                    <div className="mt-2 grid gap-3 text-sm">
                      {identitySummary("Rollback owner", report.rollback_owner)}
                      <p>Rollback permitted: {report.rollback.permitted ? "yes" : "no"}</p>
                      <p>Confidence window remaining: {report.rollback.confidence_window_remaining}</p>
                      <p>Latest rollback outcome: {report.rollback.latest_reason ?? "none"}</p>
                    </div>
                  </section>

                  {rollbackAvailable && (
                    <section className="min-w-0 rounded-card border border-warn bg-inset p-4">
                      <h3 className="font-bold">Roll back active model</h3>
                      <label className="mt-2 grid gap-1 text-sm" htmlFor="mpc-rollback-reason">
                        Required rollback reason
                        <input
                          id="mpc-rollback-reason"
                          className={`min-w-0 rounded-lg border border-card-border bg-card px-3 py-2 text-text ${FOCUS_RING}`}
                          value={rollbackReason}
                          onChange={(event) => setRollbackReason(event.target.value)}
                        />
                      </label>
                      <button
                        className={`pf-modal-btn danger mt-3 ${FOCUS_RING}`}
                        type="button"
                        disabled={rollbackReason.trim() === "" || rollbackPending}
                        aria-busy={rollbackPending || undefined}
                        onClick={() => void runRollback()}
                      >
                        {rollbackPending ? "Rolling back model…" : "Roll back to last safe model"}
                      </button>
                    </section>
                  )}

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Cook refit</h3>
                    <div className="mt-2 grid gap-1 text-sm">
                      <p>{report.cook_refit.authorized ? "Authorized" : "Not authorized"}</p>
                      <p>Status: {report.cook_refit.status}</p>
                      <p>{report.cook_refit.outcome ?? "No cook-refit outcome yet."}</p>
                      <p>
                        {report.cook_refit.activation_timing === "next-cook-restore"
                          ? "Becomes active on next-cook restore; no live end-of-cook swap."
                          : "No end-of-cook fit is authorized."}
                      </p>
                    </div>
                  </section>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Prediction scores</h3>
                    {report.scores.length === 0 ? (
                      <p className="mt-2 text-probe-label">No eligible score rows yet.</p>
                    ) : (
                      <>
                        <table className="mt-3 hidden w-full table-fixed text-left text-sm md:table">
                          <thead className="text-label">
                            <tr>
                              <th className="p-2" scope="col">Horizon</th>
                              <th className="p-2" scope="col">Band / phase</th>
                              <th className="p-2" scope="col">Challenger</th>
                              <th className="p-2" scope="col">Incumbent</th>
                              <th className="p-2" scope="col">Bias / band error</th>
                              <th className="p-2" scope="col">95% upper</th>
                            </tr>
                          </thead>
                          <tbody>
                            {report.scores.map((score) => (
                              <tr className="border-t border-card-border" key={`${score.horizon_steps}-${score.temperature_band}-${score.phase}-${score.ambient_source}-${score.candidate_generation}`}>
                                <td className="break-words p-2">{score.horizon_steps}</td>
                                <td className="break-words p-2">{score.temperature_band} / {score.phase}</td>
                                <td className="break-words p-2">{metric(score.challenger_rmse_c, " °C")}</td>
                                <td className="break-words p-2">{metric(score.incumbent_rmse_c, " °C")}</td>
                                <td className="break-words p-2">
                                  C {metric(score.challenger_bias_c, " °C")} / {metric(score.challenger_band_error_c, " °C")}
                                  <br />I {metric(score.incumbent_bias_c, " °C")} / {metric(score.incumbent_band_error_c, " °C")}
                                </td>
                                <td className="break-words p-2">{metric(score.bootstrap.rmse_ratio_upper_bound)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <div className="mt-3 grid gap-3 md:hidden">
                          {report.scores.map((score) => (
                            <dl className="grid grid-cols-2 gap-2 rounded-lg border border-card-border bg-card p-3 text-sm" key={`${score.horizon_steps}-${score.temperature_band}-${score.phase}-${score.ambient_source}-${score.candidate_generation}`}>
                              <ScoreDetails score={score} />
                            </dl>
                          ))}
                        </div>
                      </>
                    )}
                  </section>

                  <div className="grid gap-4 md:grid-cols-2">
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Target timing</h3>
                      <p className="mt-2 text-sm">Status: {report.target_timing.status}</p>
                      {report.target_timing.available ? (
                        <>
                          <p className="mt-2 break-words">{report.target_timing.hardware_provenance}</p>
                          <p className="mt-1 text-sm">
                            {report.target_timing.sample_count} samples · p50 {metric(report.target_timing.p50_ms, " ms")} · p95 {metric(report.target_timing.p95_ms, " ms")} · p99 {metric(report.target_timing.p99_ms, " ms")}
                          </p>
                        </>
                      ) : (
                        <p className="mt-2 text-probe-label">
                          No target-hardware timing evidence. Workstation timing cannot satisfy this gate.
                        </p>
                      )}
                    </section>
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Ambient provenance</h3>
                      <p className="mt-2 text-sm">
                        {report.ambient_provenance_limitation ?? "All scored ambient evidence has measured provenance."}
                      </p>
                    </section>
                  </div>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Lifecycle</h3>
                    {report.lifecycle.length === 0 ? (
                      <p className="mt-2 text-probe-label">No lifecycle entries yet.</p>
                    ) : (
                      <ol className="mt-2 grid gap-2 text-sm">
                        {report.lifecycle.map((entry) => (
                          <li className="border-l-2 border-card-border pl-3" key={`${entry.timestamp_ms}-${entry.phase}`}>
                            <strong>{entry.phase}</strong>
                            {entry.reason ? ` — ${entry.reason}` : ""}
                            <span className="block text-probe-label">
                              Role {entry.role_generation} · candidate {entry.candidate_generation ?? "none"}
                            </span>
                          </li>
                        ))}
                      </ol>
                    )}
                  </section>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">History</h3>
                    {report.history.length === 0 ? (
                      <p className="mt-2 text-probe-label">No history entries yet.</p>
                    ) : (
                      <ol className="mt-2 grid gap-3 text-sm">
                        {report.history.map((entry) => (
                          <li
                            className="grid gap-1 border-l-2 border-card-border pl-3"
                            key={`${entry.timestamp_ms}-${entry.evidence_id}-${entry.event}`}
                          >
                            <strong>Event: {entry.event}</strong>
                            <span>Reason: {entry.reason ?? "none"}</span>
                            <span className="break-all">Evidence ID: {entry.evidence_id}</span>
                            <span className="break-all">
                              Decision ID: {entry.decision_id ?? "none"}
                            </span>
                            <span>
                              Role generation: {entry.role_generation ?? "none"}
                            </span>
                            <span>
                              Candidate generation: {entry.candidate_generation ?? "none"}
                            </span>
                            <span className="text-probe-label">
                              Timestamp: {entry.timestamp_ms}
                            </span>
                          </li>
                        ))}
                      </ol>
                    )}
                  </section>
                </>
              )}
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
