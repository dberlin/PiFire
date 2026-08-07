import { useCallback, useEffect, useRef, useState } from "react";
import {
  activateModel,
  fetchModelEvidenceReport,
  rollbackModel,
  setMpcCalibration,
} from "../../helpers/modelEvidence/modelEvidenceApi";
import type {
  ModelEvidenceReport,
  ModelEvidenceStatus,
  ModelScore,
  MpcCalibrationAction,
  TemperatureUnit,
} from "../../helpers/modelEvidence/types";

const REPORT_REFRESH_MS = 5_000;
const DEFAULT_MAXIMUM_F = 425;
const MINIMUM_MAXIMUM: Record<TemperatureUnit, number> = { F: 225, C: 107 };

const STATUS_LABEL: Record<ModelEvidenceStatus, string> = {
  collecting: "Collecting",
  "insufficient-excitation": "Insufficient excitation",
  fitting: "Fitting",
  evaluating: "Evaluating",
  "ready-for-review": "Ready for review",
  active: "Active",
  fallback: "Fallback",
  "schema-invalidated": "Schema invalidated",
};

const STATUS_TONE: Record<ModelEvidenceStatus, string> = {
  collecting: "text-probe-label",
  "insufficient-excitation": "text-warn",
  fitting: "text-accent",
  evaluating: "text-accent",
  "ready-for-review": "text-ok",
  active: "text-ok",
  fallback: "text-warn",
  "schema-invalidated": "text-danger",
};

interface MpcLearningPanelProps {
  apiBase: string;
  selectedController: string | null;
  units: TemperatureUnit;
  safetyMaxTemp: number;
  ambientC: number;
}

function metric(value: number | null, suffix = ""): string {
  return value === null ? "Unavailable" : `${value.toFixed(2)}${suffix}`;
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
  safetyMaxTemp,
  ambientC,
}: MpcLearningPanelProps) {
  const initialMaximum =
    units === "F"
      ? Math.min(DEFAULT_MAXIMUM_F, safetyMaxTemp - 1)
      : Math.min(((DEFAULT_MAXIMUM_F - 32) * 5) / 9, safetyMaxTemp - 1);
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<ModelEvidenceReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportError, setReportError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [maximumTemperature, setMaximumTemperature] = useState(String(initialMaximum));
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
  const triggerButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const confirmationIdentity = useRef<{
    candidateDigest?: string | null;
    decisionId?: string | null;
  }>({});

  const refreshReport = useCallback(async () => {
    const generation = ++requestGeneration.current;
    const result = await fetchModelEvidenceReport(apiBase);
    if (generation !== requestGeneration.current) return;
    setLoading(false);
    if (!result.ok || result.data === null) {
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

  const invalidateReportRequests = useCallback(() => {
    requestGeneration.current += 1;
  }, []);

  useEffect(() => {
    const initialRefresh = window.setTimeout(() => void refreshReport(), 0);
    const interval = window.setInterval(() => void refreshReport(), REPORT_REFRESH_MS);
    return () => {
      invalidateReportRequests();
      window.clearTimeout(initialRefresh);
      window.clearInterval(interval);
    };
  }, [invalidateReportRequests, refreshReport]);

  useEffect(() => {
    if (!open) {
      if (wasOpen.current) triggerButton.current?.focus();
      wasOpen.current = false;
      return;
    }
    wasOpen.current = true;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    const candidateDigest = report?.candidate.digest;
    const decisionId = report?.decision_id;
    const previous = confirmationIdentity.current;
    if (previous.candidateDigest === candidateDigest && previous.decisionId === decisionId) return;
    confirmationIdentity.current = { candidateDigest, decisionId };
    setCandidateDigestConfirmation("");
    setDecisionIdConfirmation("");
  }, [report?.candidate.digest, report?.decision_id]);

  const maximum = Number(maximumTemperature);
  const maximumValid =
    maximumTemperature.trim() !== "" &&
    Number.isFinite(maximum) &&
    maximum >= MINIMUM_MAXIMUM[units] &&
    maximum < safetyMaxTemp;
  const calibrationRunning =
    report?.calibration.status === "active" || report?.calibration.status === "running";
  const calibrationPaused = report?.calibration.status === "paused";
  const startDisabled =
    report === null ||
    calibrationRunning ||
    calibrationPaused ||
    !maximumValid ||
    !emptyGrill ||
    !pellets ||
    pendingActions.has("start");
  const activationReady =
    report?.status === "ready-for-review" &&
    report.candidate.digest !== null &&
    report.decision_id !== null;
  const activationConfirmed =
    activationReady &&
    candidateDigestConfirmation === report.candidate.digest &&
    decisionIdConfirmation === report.decision_id;

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
        maximum_temperature: maximumValid ? maximum : initialMaximum,
        temperature_unit: units,
        ambient_c: ambientC,
        ambient_source: "configured",
        // The legacy command envelope requires these fields for every action.
        // They gate Start in this UI; other actions remain independent so Stop
        // cannot be trapped behind start-only acknowledgements.
        empty_grill_confirmed: isStart ? emptyGrill : true,
        pellets_confirmed: isStart ? pellets : true,
      },
      apiBase,
    );
    setPendingActions((current) => {
      const next = new Set(current);
      next.delete(action);
      return next;
    });
    if (!result.ok) {
      setActionError(result.message || `${action} was not accepted`);
      await loadReport();
      return;
    }
    await loadReport();
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
    setActivationPending(false);
    if (!result.ok) {
      setActionError(result.message || "Model activation was not accepted");
    } else {
      setCandidateDigestConfirmation("");
      setDecisionIdConfirmation("");
    }
    await loadReport();
  };

  const runRollback = async () => {
    const reason = rollbackReason.trim();
    if (reason === "" || rollbackPending) return;
    setRollbackPending(true);
    setActionError(null);
    const result = await rollbackModel({ reason }, apiBase);
    setRollbackPending(false);
    if (!result.ok) {
      setActionError(result.message || "Model rollback was not accepted");
    } else {
      setRollbackReason("");
    }
    await loadReport();
  };

  const triggerStatus =
    loading && report === null
      ? "loading"
      : report
        ? STATUS_LABEL[report.status].toLowerCase()
        : "unavailable";

  return (
    <>
      <button
        ref={triggerButton}
        className="pf-btn"
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-busy={(loading && report === null) || undefined}
        onClick={() => setOpen(true)}
      >
        MPC learning: {triggerStatus}
      </button>
      {open && (
        <div className="pf-modal-scrim" onClick={() => setOpen(false)}>
          <section
            className="pf-modal w-11/12 max-w-5xl min-w-0 text-text"
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
                  <p className={`mt-1 font-semibold ${STATUS_TONE[report.status]}`}>
                    {STATUS_LABEL[report.status]}
                  </p>
                )}
              </div>
              <button
                ref={closeButton}
                className="pf-toggle shrink-0"
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
                  className="pf-modal-btn mt-2"
                  type="button"
                  onClick={() => void loadReport()}
                >
                  Retry evidence report
                </button>
              </div>
            )}

            {report !== null && (
              <>
                <section className="rounded-card border border-card-border bg-inset p-4">
                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <label className="font-semibold" htmlFor="mpc-calibration-maximum">
                        Maximum calibration temperature (°{units})
                      </label>
                      <input
                        id="mpc-calibration-maximum"
                        className="mt-1 w-full rounded-lg border border-card-border bg-card px-3 py-2 text-text focus:outline-none focus:ring-2 focus:ring-accent"
                        type="number"
                        inputMode="decimal"
                        min={MINIMUM_MAXIMUM[units]}
                        max={safetyMaxTemp - 1}
                        value={maximumTemperature}
                        aria-describedby="mpc-calibration-maximum-help"
                        onChange={(event) => setMaximumTemperature(event.target.value)}
                      />
                      <p
                        className="mt-1 text-sm font-normal text-probe-label"
                        id="mpc-calibration-maximum-help"
                      >
                        Enter {MINIMUM_MAXIMUM[units]}–{safetyMaxTemp - 1} °{units}; the maximum
                        stays below the configured shutdown limit.
                      </p>
                    </div>
                    <div className="grid content-start gap-2 text-sm">
                      <label className="flex items-start gap-2">
                        <input
                          className="mt-1 accent-accent"
                          type="checkbox"
                          checked={emptyGrill}
                          onChange={(event) => setEmptyGrill(event.target.checked)}
                        />
                        The grill is empty, with normal grates and drip tray installed.
                      </label>
                      <label className="flex items-start gap-2">
                        <input
                          className="mt-1 accent-accent"
                          type="checkbox"
                          checked={pellets}
                          onChange={(event) => setPellets(event.target.checked)}
                        />
                        Sufficient pellets are loaded for the calibration run.
                      </label>
                    </div>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                    <button
                      className="pf-modal-btn accent"
                      type="button"
                      disabled={startDisabled}
                      aria-busy={pendingActions.has("start") || undefined}
                      onClick={() => void runAction("start")}
                    >
                      {pendingActions.has("start") ? "Start calibration…" : "Start calibration"}
                    </button>
                    {calibrationPaused ? (
                      <button
                        className="pf-modal-btn"
                        type="button"
                        disabled={pendingActions.has("resume")}
                        aria-busy={pendingActions.has("resume") || undefined}
                        onClick={() => void runAction("resume")}
                      >
                        {pendingActions.has("resume")
                          ? "Resume calibration…"
                          : "Resume calibration"}
                      </button>
                    ) : (
                      <button
                        className="pf-modal-btn"
                        type="button"
                        disabled={!calibrationRunning || pendingActions.has("pause")}
                        aria-busy={pendingActions.has("pause") || undefined}
                        onClick={() => void runAction("pause")}
                      >
                        {pendingActions.has("pause") ? "Pause calibration…" : "Pause calibration"}
                      </button>
                    )}
                    <button
                      className="pf-modal-btn danger col-span-2 md:col-span-1"
                      type="button"
                      disabled={pendingActions.has("stop")}
                      aria-busy={pendingActions.has("stop") || undefined}
                      onClick={() => void runAction("stop")}
                    >
                      {pendingActions.has("stop") ? "Stop calibration…" : "Stop calibration"}
                    </button>
                  </div>
                  {actionError !== null && (
                    <p className="mt-3 text-danger" role="alert">
                      {actionError}
                    </p>
                  )}
                </section>

                {(report.calibration.timed_out || report.calibration.incomplete) && (
                  <div className="rounded-lg border border-warn p-3 text-warn" role="alert">
                    {report.calibration.timed_out && <p>Calibration stage timed out.</p>}
                    {report.calibration.incomplete && <p>Calibration is incomplete.</p>}
                  </div>
                )}

                <div className="grid gap-4 md:grid-cols-2">
                  <section className="min-w-0 rounded-card border border-card-border bg-inset p-4">
                    <h3 className="font-bold">Candidate and progress</h3>
                    {report.candidate.generation === null ? (
                      <p className="mt-2 text-probe-label">No candidate yet.</p>
                    ) : (
                      <p className="mt-2">Candidate generation {report.candidate.generation}</p>
                    )}
                    <p className="break-all text-sm text-probe-label">
                      {report.candidate.digest ?? "Digest unavailable"}
                    </p>
                    <div className="mt-3 grid gap-1 border-t border-card-border pt-3 text-sm">
                      <p>Active model: {report.active_model.kind}</p>
                      <p className="break-all text-probe-label">
                        {report.active_model.digest ?? "Active digest unavailable"}
                      </p>
                      <p>Default model: {report.default_model.kind}</p>
                      <p className="break-all text-probe-label">
                        {report.default_model.digest ?? "Default digest unavailable"}
                      </p>
                    </div>
                    <div className="mt-3 grid gap-1 text-sm">
                      <p>Stage: {report.calibration.stage ?? "not started"}</p>
                      <p>
                        Current probe:{" "}
                        {report.calibration.current_probe === null
                          ? "none"
                          : `${report.calibration.current_probe >= 0 ? "+" : ""}${report.calibration.current_probe.toFixed(3)} q`}
                      </p>
                      <p>
                        {report.calibration.eligible_count} eligible /{" "}
                        {report.calibration.ineligible_count} ineligible
                      </p>
                      <p>
                        Completed stages: {report.calibration.completed_stages.join(", ") || "none"}
                      </p>
                      <p>
                        Missing stages: {report.calibration.missing_stages.join(", ") || "none"}
                      </p>
                      {report.calibration.ineligible_reasons.length > 0 && (
                        <p>
                          Ineligible reasons: {report.calibration.ineligible_reasons.join(", ")}
                        </p>
                      )}
                    </div>
                  </section>

                  <section className="min-w-0 rounded-card border border-card-border bg-inset p-4">
                    <h3 className="font-bold">Readiness</h3>
                    <p className="mt-2 text-sm">
                      Missing gates: {report.missing_gates.join(", ") || "none"}
                    </p>
                    {report.blockers.length === 0 ? (
                      <p className="mt-2 text-ok">No current blockers.</p>
                    ) : (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                        {report.blockers.map((blocker) => (
                          <li key={blocker}>{blocker}</li>
                        ))}
                      </ul>
                    )}
                    <p className="mt-3 text-sm text-probe-label">
                      Identifiability:{" "}
                      {report.identifiability.accepted ? "accepted" : "not accepted"}
                      {report.identifiability.reason ? ` — ${report.identifiability.reason}` : ""}
                    </p>
                  </section>
                </div>

                {activationReady && (
                  <section className="min-w-0 rounded-card border border-ok bg-inset p-4">
                    <h3 className="font-bold">Activate reviewed model</h3>
                    <p className="mt-2 text-sm">
                      Confirm both exact values. Preparation and persistence do not alter the
                      shipped grey-box default.
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
                          className="min-w-0 rounded-lg border border-card-border bg-card px-3 py-2 font-mono text-xs text-text"
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
                          className="min-w-0 rounded-lg border border-card-border bg-card px-3 py-2 font-mono text-xs text-text"
                          autoComplete="off"
                          spellCheck={false}
                          value={decisionIdConfirmation}
                          onChange={(event) => setDecisionIdConfirmation(event.target.value)}
                        />
                      </label>
                    </div>
                    <button
                      className="pf-modal-btn accent mt-3"
                      type="button"
                      disabled={!activationConfirmed || activationPending}
                      aria-busy={activationPending || undefined}
                      onClick={() => void runActivation()}
                    >
                      {activationPending ? "Activating exact model…" : "Activate exact model"}
                    </button>
                  </section>
                )}

                {report.status === "active" && (
                  <section className="min-w-0 rounded-card border border-warn bg-inset p-4">
                    <h3 className="font-bold">Roll back active model</h3>
                    <label className="mt-2 grid gap-1 text-sm" htmlFor="mpc-rollback-reason">
                      Required rollback reason
                      <input
                        id="mpc-rollback-reason"
                        className="min-w-0 rounded-lg border border-card-border bg-card px-3 py-2 text-text"
                        value={rollbackReason}
                        onChange={(event) => setRollbackReason(event.target.value)}
                      />
                    </label>
                    <button
                      className="pf-modal-btn danger mt-3"
                      type="button"
                      disabled={rollbackReason.trim() === "" || rollbackPending}
                      aria-busy={rollbackPending || undefined}
                      onClick={() => void runRollback()}
                    >
                      {rollbackPending ? "Rolling back model…" : "Roll back to last safe model"}
                    </button>
                  </section>
                )}

                {report.history.length > 0 &&
                  report.history.at(-1)?.reason !== null &&
                  report.history.at(-1)?.reason !== undefined && (
                    <p className="rounded-lg border border-warn p-3 text-warn" role="status">
                      Latest model decision: {report.history.at(-1)?.reason}
                    </p>
                  )}

                <section className="min-w-0 rounded-card border border-card-border bg-inset p-4">
                  <h3 className="font-bold">Prediction scores</h3>
                  {report.scores.length === 0 ? (
                    <p className="mt-2 text-probe-label">No eligible score rows yet.</p>
                  ) : (
                    <>
                      <table className="mt-3 hidden w-full table-fixed text-left text-sm md:table">
                        <thead className="text-label">
                          <tr>
                            <th className="p-2">Horizon</th>
                            <th className="p-2">Band / phase</th>
                            <th className="p-2">Challenger</th>
                            <th className="p-2">Incumbent</th>
                            <th className="p-2">Bias / band error</th>
                            <th className="p-2">95% upper</th>
                          </tr>
                        </thead>
                        <tbody>
                          {report.scores.map((score) => (
                            <tr
                              className="border-t border-card-border"
                              key={`${score.horizon_steps}-${score.temperature_band}-${score.phase}-${score.ambient_source}-${score.generation}`}
                            >
                              <td className="break-words p-2">{score.horizon_steps}</td>
                              <td className="break-words p-2">
                                {score.temperature_band} / {score.phase}
                              </td>
                              <td className="break-words p-2">
                                {metric(score.challenger_rmse_c, " °C")}
                              </td>
                              <td className="break-words p-2">
                                {metric(score.incumbent_rmse_c, " °C")}
                              </td>
                              <td className="break-words p-2">
                                C {metric(score.challenger_bias_c, " °C")} /{" "}
                                {metric(score.challenger_band_error_c, " °C")}
                                <br />I {metric(score.incumbent_bias_c, " °C")} /{" "}
                                {metric(score.incumbent_band_error_c, " °C")}
                              </td>
                              <td className="break-words p-2">
                                {metric(score.bootstrap.rmse_ratio_upper_bound)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div className="mt-3 grid gap-3 md:hidden">
                        {report.scores.map((score) => (
                          <dl
                            className="grid grid-cols-2 gap-2 rounded-lg border border-card-border bg-card p-3 text-sm"
                            key={`${score.horizon_steps}-${score.temperature_band}-${score.phase}-${score.ambient_source}-${score.generation}`}
                          >
                            <ScoreDetails score={score} />
                          </dl>
                        ))}
                      </div>
                    </>
                  )}
                </section>

                <div className="grid gap-4 md:grid-cols-2">
                  <section className="min-w-0 rounded-card border border-card-border bg-inset p-4">
                    <h3 className="font-bold">Target timing</h3>
                    {report.target_timing.available ? (
                      <>
                        <p className="mt-2 break-words">
                          {report.target_timing.hardware_provenance}
                        </p>
                        <p className="mt-1 text-sm">
                          {report.target_timing.sample_count} samples · p50{" "}
                          {metric(report.target_timing.p50_ms, " ms")} · p95{" "}
                          {metric(report.target_timing.p95_ms, " ms")} · p99{" "}
                          {metric(report.target_timing.p99_ms, " ms")}
                        </p>
                      </>
                    ) : (
                      <p className="mt-2 text-probe-label">
                        No target-hardware timing evidence. Workstation timing cannot satisfy this
                        gate.
                      </p>
                    )}
                  </section>
                  <section className="min-w-0 rounded-card border border-card-border bg-inset p-4">
                    <h3 className="font-bold">Ambient provenance</h3>
                    <p className="mt-2 text-sm">
                      {report.ambient_provenance_limitation ??
                        "All scored ambient evidence has measured provenance."}
                    </p>
                  </section>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </>
  );
}
