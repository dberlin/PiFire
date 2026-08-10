import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  activateModel,
  fetchModelEvidenceReport,
  rollbackModel,
  setMpcCalibration,
} from "../../helpers/modelEvidence/modelEvidenceApi";
import type {
  CheckStatus,
  ModelEvidenceStatus,
  MpcCalibrationAction,
  TemperatureUnit,
} from "../../helpers/modelEvidence/types";

const REPORT_REFRESH_MS = 5_000;
const BAND_CENTERS_F = [225, 325, 425];
const FOCUS_RING =
  "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent";
const REPORT_SECTION = "min-w-0 rounded-card border border-card-border bg-inset p-4";
const REPORT_QUERY_ROOT = "model-evidence-report";
const CALIBRATION_ACTION_LABEL: Record<MpcCalibrationAction, string> = {
  start: "Start calibration",
  pause: "Pause calibration",
  resume: "Resume calibration",
  stop: "Stop calibration",
  "reset-progress": "Reset calibration progress",
};

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
  /** Socket invalidation high-water. REST remains the only rendered authority. */
  learningReportRevision?: number;
}

class ReportRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReportRequestError";
  }
}

function bandCenters(units: TemperatureUnit): string {
  const values = BAND_CENTERS_F.map((f) => (units === "F" ? f : Math.round(((f - 32) * 5) / 9)));
  return `${values[0]}, ${values[1]} and ${values[2]} °${units}`;
}

function shown(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "not reported" : String(value);
}

function yesNo(value: boolean): string {
  return value ? "yes" : "no";
}

function checkTone(status: CheckStatus): string {
  if (status === "passed") return "text-ok";
  if (status === "failed") return "text-danger";
  if (status === "pending") return "text-accent";
  return "text-probe-label";
}

function generationIdentity(label: string, digest: string | null, generation: number | null) {
  return (
    <div className="grid min-w-0 gap-1">
      <p className="font-semibold">{label}</p>
      <p className="break-all font-mono text-xs text-probe-label">{digest ?? "none"}</p>
      <p className="text-probe-label">Generation: {generation ?? "none"}</p>
    </div>
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
  const queryClient = useQueryClient();
  const queryKey = useMemo(() => [REPORT_QUERY_ROOT, apiBase] as const, [apiBase]);
  const requestGeneration = useRef(0);
  const lastLearningReportRevision = useRef(learningReportRevision);
  const [open, setOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [emptyGrill, setEmptyGrill] = useState(false);
  const [pellets, setPellets] = useState(false);
  const [pendingActions, setPendingActions] = useState<Set<MpcCalibrationAction>>(new Set());
  const [candidateDigestConfirmation, setCandidateDigestConfirmation] = useState("");
  const [decisionIdConfirmation, setDecisionIdConfirmation] = useState("");
  const [activationPending, setActivationPending] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackPending, setRollbackPending] = useState(false);
  const nextCalibrationRevision = useRef(0);
  const triggerButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);
  const confirmationIdentity = useRef<{
    candidateDigest: string | null;
    decisionId: string | null;
  }>({
    candidateDigest: null,
    decisionId: null,
  });

  const {
    data: report,
    error: reportQueryError,
    isPending,
    refetch,
  } = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const generation = ++requestGeneration.current;
      const result = await fetchModelEvidenceReport(apiBase, signal);
      if (signal.aborted || generation !== requestGeneration.current) {
        throw new DOMException("Superseded model-evidence report request", "AbortError");
      }
      if (!result.ok || result.data === null) {
        throw new ReportRequestError(result.message || "Model evidence report unavailable");
      }
      return result.data;
    },
    refetchInterval: REPORT_REFRESH_MS,
    retry: false,
  });

  const reportError =
    reportQueryError instanceof Error
      ? reportQueryError.message
      : reportQueryError === null
        ? null
        : "Model evidence report unavailable";

  const refreshReport = useCallback(async () => {
    requestGeneration.current += 1;
    await refetch({ cancelRefetch: true });
  }, [refetch]);

  useEffect(() => {
    if (lastLearningReportRevision.current === learningReportRevision) return;
    lastLearningReportRevision.current = learningReportRevision;
    // Invalidate the observed query rather than creating a socket-owned report.
    // Cancel first even when the initial request has no data: React Query
    // otherwise deduplicates the invalidation onto that older promise.
    requestGeneration.current += 1;
    void queryClient
      .cancelQueries({ queryKey, exact: true })
      .then(() => queryClient.invalidateQueries({ queryKey, exact: true }));
  }, [learningReportRevision, queryClient, queryKey]);

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
      const focusIsInside =
        document.activeElement !== null &&
        dialog.current?.contains(document.activeElement) === true;
      if (!focusIsInside) {
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

  const activationReady =
    report?.status === "ready-for-review" &&
    report.mode === "operator-calibration" &&
    report.candidate.origin === "operator-calibration" &&
    report.candidate.policy === "operator-reviewed" &&
    report.candidate.digest !== null &&
    report.decision_id !== null;
  const activationConfirmed =
    activationReady &&
    candidateDigestConfirmation === report.candidate.digest &&
    decisionIdConfirmation === report.decision_id;
  const rollbackAvailable =
    report?.activation.phase === "active" &&
    report.identities.rollback_digest !== null &&
    report.identities.rollback_generation !== null;

  const runCalibrationAction = async (action: MpcCalibrationAction) => {
    if (pendingActions.has(action)) return;
    const revision =
      Math.max(nextCalibrationRevision.current, report?.calibration.command_high_water ?? 0) + 1;
    nextCalibrationRevision.current = revision;
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
    await refreshReport();
    setPendingActions((current) => {
      const next = new Set(current);
      next.delete(action);
      return next;
    });
  };

  const runActivation = async () => {
    if (!activationConfirmed || activationPending || report === undefined) return;
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
      const detail = result.data && "detail" in result.data ? result.data.detail : null;
      setActionError(detail || result.message || "Model activation was not accepted");
    } else {
      setCandidateDigestConfirmation("");
      setDecisionIdConfirmation("");
    }
    await refreshReport();
    setActivationPending(false);
  };

  const runRollback = async () => {
    const reason = rollbackReason.trim();
    if (reason === "" || rollbackPending || !rollbackAvailable) return;
    setRollbackPending(true);
    setActionError(null);
    const result = await rollbackModel({ reason }, apiBase);
    if (!result.ok || result.data?.accepted === false) {
      const detail = result.data && "detail" in result.data ? result.data.detail : null;
      setActionError(detail || result.message || "Model rollback was not accepted");
    } else {
      setRollbackReason("");
    }
    await refreshReport();
    setRollbackPending(false);
  };

  const triggerStatus =
    reportError !== null
      ? "error"
      : isPending && report === undefined
        ? "loading"
        : report
          ? STATUS_LABEL[report.status].toLowerCase()
          : "unavailable";
  const assessment = report?.candidate.assessment ?? null;
  const parameterEntries = report?.candidate.parameters
    ? Object.entries(report.candidate.parameters)
    : [];
  const deltaEntries = report?.candidate.parameter_deltas
    ? Object.entries(report.candidate.parameter_deltas)
    : [];

  return (
    <>
      <button
        ref={triggerButton}
        className={`pf-btn pf-dash-mpc-learning ${FOCUS_RING}`}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-busy={(isPending && report === undefined) || undefined}
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
                  {report && (
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

              {isPending && report === undefined && <p role="status">Loading model evidence…</p>}
              {reportError !== null && (
                <div className="rounded-lg border border-danger p-3 text-danger" role="alert">
                  <p>{reportError}</p>
                  <button
                    className={`pf-modal-btn mt-2 ${FOCUS_RING}`}
                    type="button"
                    onClick={() => void refreshReport()}
                  >
                    Retry evidence report
                  </button>
                </div>
              )}

              {report && (
                <>
                  {(report.errors.length > 0 || report.failure !== null) && (
                    <div
                      className="grid gap-2 rounded-lg border border-danger p-3 text-danger"
                      role="alert"
                    >
                      {report.errors.map((error) => (
                        <p key={error}>Report error: {error}</p>
                      ))}
                      {report.failure && (
                        <p>
                          <strong>{report.failure.code}</strong> — {report.failure.detail}
                          {report.failure.terminal ? " — terminal" : ""}
                        </p>
                      )}
                    </div>
                  )}

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Operator calibration commands</h3>
                    <p className="mt-2 text-sm text-probe-label">
                      Calibration probes around the active Hold at {bandCenters(units)}. The report
                      serializes command high-water only; this panel does not invent a command
                      phase.
                    </p>
                    <p className="mt-2 text-sm">
                      Accepted command high-water: {report.calibration.command_high_water}
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
                      {(["start", "pause", "resume", "stop", "reset-progress"] as const).map(
                        (action) => {
                          const pending = pendingActions.has(action);
                          const startBlocked = action === "start" && (!emptyGrill || !pellets);
                          return (
                            <button
                              key={action}
                              className={`pf-modal-btn ${
                                action === "start" ? "accent" : action === "stop" ? "danger" : ""
                              } ${FOCUS_RING}`}
                              type="button"
                              disabled={pending || startBlocked}
                              aria-busy={pending || undefined}
                              onClick={() => void runCalibrationAction(action)}
                            >
                              {pending
                                ? `${CALIBRATION_ACTION_LABEL[action]}…`
                                : CALIBRATION_ACTION_LABEL[action]}
                            </button>
                          );
                        },
                      )}
                    </div>
                    {actionError && (
                      <p className="mt-3 text-danger" role="alert">
                        {actionError}
                      </p>
                    )}
                  </section>

                  <div className="grid gap-4 md:grid-cols-2">
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Current authority</h3>
                      <div className="mt-2 grid gap-2 text-sm">
                        <p>Mode: {report.mode ?? "none"}</p>
                        <p>Role generation: {report.candidate.role_generation ?? "none"}</p>
                        <p>
                          Candidate generation: {report.candidate.candidate_generation ?? "none"}
                        </p>
                        <p>Candidate policy: {report.candidate.policy ?? "none"}</p>
                        <p className="break-all font-mono text-xs">
                          Candidate digest: {report.candidate.digest ?? "none"}
                        </p>
                        <p className="break-all font-mono text-xs">
                          Decision: {report.decision_id ?? "none"}
                        </p>
                        <p className="break-all font-mono text-xs text-probe-label">
                          Report revision: {report.revision}
                        </p>
                      </div>
                    </section>
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Evidence authority</h3>
                      <div className="mt-2 grid gap-1 text-sm">
                        <p>Current evidence: {report.evidence.count}</p>
                        <p>Audit evidence: {report.evidence.audit_count}</p>
                        <p>Retired schema entries excluded: {report.evidence.retired_excluded}</p>
                        <p>High-water: {report.evidence.high_water?.join(" / ") ?? "none"}</p>
                      </div>
                    </section>
                  </div>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Fit and evidence window</h3>
                    <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
                      <p>Status: {report.fit.status}</p>
                      <p>Request: {report.fit.request_id ?? "none"}</p>
                      <p>Window ID: {report.fit.window_id ?? "none"}</p>
                      <p className={report.fit.error ? "text-danger" : "text-probe-label"}>
                        Fit error: {report.fit.error ?? "none"}
                      </p>
                      {report.window && (
                        <>
                          <p>Session: {report.window.session_id}</p>
                          <p>Cook: {report.window.cook_id ?? "none"}</p>
                          <p>
                            Observation sequence: {report.window.first_observation_sequence}–
                            {report.window.last_observation_sequence}
                          </p>
                          <p>Window role generation: {report.window.role_generation}</p>
                          <p className="break-all">
                            Configuration: {report.window.configuration_digest}
                          </p>
                          <p className="break-all">Incumbent: {report.window.incumbent_digest}</p>
                        </>
                      )}
                    </div>
                  </section>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Grey candidate</h3>
                    {parameterEntries.length === 0 ? (
                      <p className="mt-2 text-probe-label">No candidate parameters reported.</p>
                    ) : (
                      <div className="mt-3 overflow-x-auto">
                        <table
                          className="w-full text-left text-sm"
                          aria-label="Grey candidate parameters"
                        >
                          <thead className="text-label">
                            <tr>
                              <th className="p-2" scope="col">
                                Parameter
                              </th>
                              <th className="p-2" scope="col">
                                Candidate
                              </th>
                              <th className="p-2" scope="col">
                                Delta
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {parameterEntries.map(([name, candidateValue]) => {
                              const delta = deltaEntries.find(
                                ([deltaName]) => deltaName === name,
                              )?.[1];
                              return (
                                <tr className="border-t border-card-border" key={name}>
                                  <th className="p-2" scope="row">
                                    {name}
                                  </th>
                                  <td className="p-2">{candidateValue}</td>
                                  <td className="p-2">{shown(delta)}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                    <p className="mt-2 text-sm">
                      Fit quality: {shown(report.candidate.fit_quality)}
                    </p>
                    <p className="text-sm">
                      Identifiability: {shown(report.candidate.identifiability)}
                    </p>
                  </section>

                  <div className="grid gap-4 md:grid-cols-2">
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Native candidate and timing</h3>
                      <div className="mt-2 grid gap-1 text-sm">
                        <p
                          className={
                            assessment ? checkTone(assessment.native_build) : "text-probe-label"
                          }
                        >
                          Native build: {assessment?.native_build ?? "not reported"}
                        </p>
                        <p
                          className={
                            assessment ? checkTone(assessment.native_dry_solve) : "text-probe-label"
                          }
                        >
                          Native dry solve: {assessment?.native_dry_solve ?? "not reported"}
                        </p>
                        <p
                          className={
                            assessment ? checkTone(assessment.target_timing) : "text-probe-label"
                          }
                        >
                          Target timing: {assessment?.target_timing ?? "not reported"}
                        </p>
                        <p>Ambient provenance: not reported by backend</p>
                      </div>
                    </section>
                    <section className={REPORT_SECTION}>
                      <h3 className="font-bold">Readiness and rejection</h3>
                      <div className="mt-2 grid gap-1 text-sm">
                        <p>
                          Fit accepted:{" "}
                          {assessment ? yesNo(assessment.fit_accepted) : "not reported"}
                        </p>
                        <p>
                          Identifiability accepted:{" "}
                          {assessment ? yesNo(assessment.identifiability_accepted) : "not reported"}
                        </p>
                        <p>
                          Confidence accepted:{" "}
                          {assessment ? yesNo(assessment.confidence_accepted) : "not reported"}
                        </p>
                        <p>
                          Rejection reasons: {assessment?.rejection_reasons.join(", ") || "none"}
                        </p>
                        <p>Blockers: {report.blockers.join(", ") || "none"}</p>
                      </div>
                    </section>
                  </div>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Backend checks and gates</h3>
                    <dl className="mt-2 grid gap-2 text-sm md:grid-cols-2">
                      {Object.entries(report.checks).map(([name, status]) => (
                        <div key={name}>
                          <dt className="font-semibold">{name}</dt>
                          <dd className={checkTone(status)}>{status}</dd>
                        </div>
                      ))}
                      {report.gates.map((gate) => (
                        <div key={gate.name}>
                          <dt className="font-semibold">{gate.name}</dt>
                          <dd className={gate.passed ? "text-ok" : "text-danger"}>
                            {gate.passed ? "passed" : "failed"}
                            {gate.reason ? ` — ${gate.reason}` : ""}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </section>

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Activation and swap</h3>
                    <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
                      <p>Durable phase: {report.activation.phase}</p>
                      <p>Policy: {report.activation.policy ?? "none"}</p>
                      <p>Origin: {report.activation.origin ?? "none"}</p>
                      <p>Persistence pending: {yesNo(report.activation.pending_persistence)}</p>
                      <p>
                        Frame-boundary swap pending:{" "}
                        {yesNo(report.activation.pending_frame_boundary_swap)}
                      </p>
                      <p className="break-all">
                        Transaction: {report.activation.transaction_id ?? "none"}
                      </p>
                      <p>Reason: {report.activation.reason ?? "none"}</p>
                      <p>
                        Latest lifecycle: {report.latest_lifecycle?.phase ?? "none"}
                        {report.latest_lifecycle?.reason
                          ? ` — ${report.latest_lifecycle.reason}`
                          : ""}
                      </p>
                    </div>
                  </section>

                  {activationReady && (
                    <section className="min-w-0 rounded-card border border-ok bg-inset p-4">
                      <h3 className="font-bold">Activate reviewed model</h3>
                      <p className="mt-2 text-sm">
                        Confirm the exact candidate digest and confidence decision serialized by
                        this report.
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
                    <div className="mt-2 grid gap-3 text-sm md:grid-cols-3">
                      {generationIdentity(
                        "Active owner",
                        report.identities.active_digest,
                        report.identities.active_generation,
                      )}
                      {generationIdentity(
                        "Candidate owner",
                        report.identities.candidate_digest,
                        report.identities.candidate_generation,
                      )}
                      {generationIdentity(
                        "Rollback owner",
                        report.identities.rollback_digest,
                        report.identities.rollback_generation,
                      )}
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
                        {rollbackPending ? "Rolling back model…" : "Roll back to explicit owner"}
                      </button>
                    </section>
                  )}

                  <section className={REPORT_SECTION}>
                    <h3 className="font-bold">Cook refit</h3>
                    <div className="mt-2 grid gap-1 text-sm">
                      <p>Status: {report.cook_refit.status}</p>
                      <p>Final outcome: {report.cook_refit.final_status}</p>
                      <p>Authorization: {report.cook_refit.authorization}</p>
                      <p>Next cook: {yesNo(report.cook_refit.next_cook)}</p>
                    </div>
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
