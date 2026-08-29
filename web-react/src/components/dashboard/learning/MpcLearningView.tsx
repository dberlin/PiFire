import type { CheckStatus, MpcCalibrationAction } from "@pifire/core/contracts/learning";
import type { Units } from "@pifire/core/settings/settingsTypes";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchModelEvidenceReport,
  rollbackModel,
  setMpcCalibration,
} from "../../../helpers/modelEvidence/modelEvidenceApi";

import { LearningDialog } from "./LearningDialog";
import { LEARNING_SECTION_CLASS } from "./learningDisplay";

const REPORT_REFRESH_MS = 5_000;
const BAND_CENTERS_F = [225, 325, 425];
const FOCUS_RING =
  "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent";
const REPORT_QUERY_ROOT = "model-evidence-report";
const CALIBRATION_ACTION_LABEL: Record<MpcCalibrationAction, string> = {
  start: "Start calibration",
  pause: "Pause calibration",
  resume: "Resume calibration",
  stop: "Stop calibration",
  "reset-progress": "Reset calibration progress",
};

interface MpcLearningViewProps {
  apiBase: string;
  selectedController: string | null;
  units: Units;
  ambientC: number;
  currentMode: string;
  displayMode: string;
  criticalError: boolean;
  /** Socket invalidation high-water. REST remains the only rendered authority. */
  modelLearningRevision?: string | null;
}

class ReportRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReportRequestError";
  }
}

function bandCenters(units: Units): string {
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

export function MpcLearningView(props: MpcLearningViewProps) {
  return props.selectedController === "mpc" ? <ActiveMpcLearningView {...props} /> : null;
}

function ActiveMpcLearningView({
  apiBase,
  units,
  ambientC,
  currentMode,
  displayMode,
  criticalError,
  modelLearningRevision,
}: MpcLearningViewProps) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(() => [REPORT_QUERY_ROOT, apiBase] as const, [apiBase]);
  const requestGeneration = useRef(0);
  const lastModelLearningRevision = useRef(modelLearningRevision);
  const [actionError, setActionError] = useState<string | null>(null);
  const [schemaInvalidated, setSchemaInvalidated] = useState(false);
  const [emptyGrill, setEmptyGrill] = useState(false);
  const [pellets, setPellets] = useState(false);
  const [pendingActions, setPendingActions] = useState<Set<MpcCalibrationAction>>(new Set());
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackPending, setRollbackPending] = useState(false);
  const nextCalibrationRevision = useRef(0);

  const {
    data: cachedReport,
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
        const invalidSchema = result.status >= 200 && result.status < 300;
        if (invalidSchema) setSchemaInvalidated(true);
        throw new ReportRequestError(result.message || "Model evidence report unavailable");
      }
      setSchemaInvalidated(false);
      return result.data;
    },
    refetchInterval: REPORT_REFRESH_MS,
    retry: false,
  });
  const report = schemaInvalidated ? undefined : cachedReport;

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
    if (lastModelLearningRevision.current === modelLearningRevision) return;
    lastModelLearningRevision.current = modelLearningRevision;
    // Invalidate the observed query rather than creating a socket-owned report.
    // Cancel first even when the initial request has no data: React Query
    // otherwise deduplicates the invalidation onto that older promise.
    requestGeneration.current += 1;
    void queryClient
      .cancelQueries({ queryKey, exact: true })
      .then(() => queryClient.invalidateQueries({ queryKey, exact: true }));
  }, [modelLearningRevision, queryClient, queryKey]);

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

  const dialogStatus =
    reportError !== null
      ? "error"
      : isPending && report === undefined
        ? "loading"
        : (report?.status ?? "unavailable");
  const candidate = report?.candidate ?? null;
  const assessment = candidate?.assessment ?? null;
  const parameterEntries = candidate?.parameters ? Object.entries(candidate.parameters) : [];
  const deltaEntries = candidate?.parameter_deltas
    ? Object.entries(candidate.parameter_deltas)
    : [];

  return (
    <LearningDialog
      controllerLabel="MPC"
      title="MPC model learning"
      closeLabel="Close MPC model learning"
      status={dialogStatus}
      currentMode={currentMode}
      displayMode={displayMode}
      criticalError={criticalError}
      loading={isPending && report === undefined}
      loadingLabel="Loading model evidence…"
      error={reportError}
      retryLabel="Retry evidence report"
      onRetry={refreshReport}
    >
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

          <section className={LEARNING_SECTION_CLASS}>
            <h3 className="font-bold">Operator calibration commands</h3>
            <p className="mt-2 text-sm text-probe-label">
              Calibration probes around the active Hold at {bandCenters(units)}. The report
              serializes command high-water only; this panel does not invent a command phase.
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
              {(["start", "pause", "resume", "stop", "reset-progress"] as const).map((action) => {
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
              })}
            </div>
            {actionError && (
              <p className="mt-3 text-danger" role="alert">
                {actionError}
              </p>
            )}
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Current authority</h3>
              <div className="mt-2 grid gap-2 text-sm">
                <p>Mode: {report.mode ?? "none"}</p>
                <p>Challenger: {candidate?.challenger_id ?? "none"}</p>
                <p>Candidate phase: {candidate?.phase ?? "none"}</p>
                <p>Role generation: {candidate?.role_generation ?? "none"}</p>
                <p>Candidate generation: {candidate?.candidate_generation ?? "none"}</p>
                <p>Candidate policy: {candidate?.policy ?? "none"}</p>
                <p className="break-all font-mono text-xs">
                  Candidate digest: {candidate?.digest ?? "none"}
                </p>
                <p className="break-all font-mono text-xs">
                  Decision: {report.decision_id ?? "none"}
                </p>
                <p className="break-all font-mono text-xs text-probe-label">
                  Report revision: {report.revision}
                </p>
              </div>
            </section>
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Evidence authority</h3>
              <div className="mt-2 grid gap-1 text-sm">
                <p>Current evidence: {report.evidence.count}</p>
                <p>Audit evidence: {report.evidence.audit_count}</p>
                <p>Retired schema entries excluded: {report.evidence.retired_excluded}</p>
                <p>High-water: {report.evidence.high_water?.join(" / ") ?? "none"}</p>
              </div>
            </section>
          </div>
          <section className={LEARNING_SECTION_CLASS}>
            <h3 className="font-bold">Causal evaluation progress</h3>
            {report.evaluation === null ? (
              <p className="mt-2 text-sm text-probe-label">
                No causal evaluation progress is currently reported.
              </p>
            ) : (
              <>
                <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
                  <p>Evaluation epoch: {report.evaluation.epoch}</p>
                  <p>Evaluation round: {report.evaluation.round}</p>
                  <p>
                    Completed horizons: {report.evaluation.completed_horizons.join(", ") || "none"}
                  </p>
                  <p>
                    Required horizons: {report.evaluation.required_horizons.join(", ") || "none"}
                  </p>
                  <p>
                    Wins: {report.evaluation.wins} / {report.evaluation.required_wins}
                  </p>
                  <p>
                    Resumed from previous cook:{" "}
                    {yesNo(report.evaluation.resumed_from_previous_cook)}
                  </p>
                </div>
                {report.evaluation.pending_origins.length === 0 ? (
                  <p className="mt-3 text-sm text-probe-label">Pending origins: none</p>
                ) : (
                  <div className="mt-3">
                    <p className="text-sm font-semibold">Pending origins</p>
                    <ul className="mt-2 grid gap-2 text-sm">
                      {report.evaluation.pending_origins.map((origin) => (
                        <li
                          className="grid gap-1 border-t border-card-border pt-2 md:grid-cols-2"
                          key={`${origin.origin_sequence}-${origin.horizon_steps}-${origin.candidate_generation}`}
                        >
                          <span>Origin sequence: {origin.origin_sequence}</span>
                          <span>Horizon: {origin.horizon_steps}</span>
                          <span>Origin role generation: {origin.role_generation}</span>
                          <span>Origin candidate generation: {origin.candidate_generation}</span>
                          <span className="break-all font-mono text-xs">
                            Incumbent: {origin.incumbent_digest}
                          </span>
                          <span className="break-all font-mono text-xs">
                            Candidate: {origin.candidate_digest}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Candidate lineage</h3>
              {candidate === null ? (
                <p className="mt-2 text-sm text-probe-label">No challenger is currently active.</p>
              ) : (
                <div className="mt-2 grid gap-1 text-sm">
                  <p>Challenger: {candidate.challenger_id}</p>
                  <p>Phase: {candidate.phase}</p>
                  <p>
                    Parent incumbent generation: {candidate.lineage.parent_incumbent_generation}
                  </p>
                  <p>Lineage candidate generation: {candidate.lineage.candidate_generation}</p>
                  <p>Trigger origin: {candidate.lineage.trigger_origin}</p>
                  <p>Fit result: {candidate.lineage.result_status}</p>
                  <p>Fit request: {candidate.lineage.request_id}</p>
                  <p className="break-all font-mono text-xs">
                    Parent incumbent: {candidate.lineage.parent_incumbent_digest}
                  </p>
                  <p className="break-all font-mono text-xs">
                    Fit corpus: {candidate.lineage.fit_corpus_digest}
                  </p>
                  <p className="break-all font-mono text-xs">
                    Candidate: {candidate.lineage.candidate_digest}
                  </p>
                </div>
              )}
            </section>
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Exact corpus identity</h3>
              <div className="mt-2 grid gap-1 text-sm">
                <p>Corpus revision: {report.corpus.revision ?? "none"}</p>
                <p className="break-all font-mono text-xs">
                  Corpus digest: {report.corpus.digest ?? "none"}
                </p>
                <p className="break-all font-mono text-xs">
                  Fit partition: {report.corpus.fit_partition_digest ?? "none"}
                </p>
              </div>
              {report.corpus.slices.length === 0 ? (
                <p className="mt-3 text-sm text-probe-label">Corpus slices: none</p>
              ) : (
                <dl className="mt-3 grid gap-3 text-sm">
                  {report.corpus.slices.map((slice) => (
                    <div
                      className="grid gap-1 border-t border-card-border pt-2"
                      key={slice.segment_id}
                    >
                      <dt className="font-semibold">{slice.segment_id}</dt>
                      <dd>Through ordinal: {slice.through_ordinal}</dd>
                      <dd className="break-all font-mono text-xs">
                        Prefix digest: {slice.prefix_digest}
                      </dd>
                      <dd>Pre-roll: {slice.pre_roll_count}</dd>
                      <dd>Scored: {slice.scored_count}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </section>
          </div>

          <section className={LEARNING_SECTION_CLASS}>
            <h3 className="font-bold">Fit request</h3>
            <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
              <p>Status: {report.fit.status}</p>
              <p>Request: {report.fit.request_id ?? "none"}</p>
              <p className="break-all font-mono text-xs">
                Fit corpus: {report.fit.fit_corpus_digest ?? "none"}
              </p>
              <p className={report.fit.error ? "text-danger" : "text-probe-label"}>
                Fit error: {report.fit.error ?? "none"}
              </p>
            </div>
          </section>

          <section className={LEARNING_SECTION_CLASS}>
            <h3 className="font-bold">Grey candidate</h3>
            {parameterEntries.length === 0 ? (
              <p className="mt-2 text-probe-label">No candidate parameters reported.</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-sm" aria-label="Grey candidate parameters">
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
                      const delta = deltaEntries.find(([deltaName]) => deltaName === name)?.[1];
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
            <p className="mt-2 text-sm">Fit quality: {shown(candidate?.fit_quality)}</p>
            <p className="text-sm">Identifiability: {shown(candidate?.identifiability)}</p>
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Native candidate and timing</h3>
              <div className="mt-2 grid gap-1 text-sm">
                <p className={assessment ? checkTone(assessment.native_build) : "text-probe-label"}>
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
                  className={assessment ? checkTone(assessment.target_timing) : "text-probe-label"}
                >
                  Target timing: {assessment?.target_timing ?? "not reported"}
                </p>
                <p>Ambient provenance: not reported by backend</p>
              </div>
            </section>
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Readiness and rejection</h3>
              <div className="mt-2 grid gap-1 text-sm">
                <p>Fit accepted: {assessment ? yesNo(assessment.fit_accepted) : "not reported"}</p>
                <p>
                  Identifiability accepted:{" "}
                  {assessment ? yesNo(assessment.identifiability_accepted) : "not reported"}
                </p>
                <p>
                  Confidence accepted:{" "}
                  {assessment ? yesNo(assessment.confidence_accepted) : "not reported"}
                </p>
                <p>Rejection reasons: {assessment?.rejection_reasons.join(", ") || "none"}</p>
                <p>Blockers: {report.blockers.join(", ") || "none"}</p>
              </div>
            </section>
          </div>

          <section className={LEARNING_SECTION_CLASS}>
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

          <section className={LEARNING_SECTION_CLASS}>
            <h3 className="font-bold">Activation and swap</h3>
            <div className="mt-2 grid gap-1 text-sm md:grid-cols-2">
              <p>Durable phase: {report.activation.phase}</p>
              <p>Policy: {report.activation.policy ?? "none"}</p>
              <p>Origin: {report.activation.origin ?? "none"}</p>
              <p>Persistence pending: {yesNo(report.activation.pending_persistence)}</p>
              <p>
                Frame-boundary swap pending: {yesNo(report.activation.pending_frame_boundary_swap)}
              </p>
              <p className="break-all">Transaction: {report.activation.transaction_id ?? "none"}</p>
              <p>Reason: {report.activation.reason ?? "none"}</p>
              <p>
                Latest lifecycle: {report.latest_lifecycle?.phase ?? "none"}
                {report.latest_lifecycle?.reason ? ` — ${report.latest_lifecycle.reason}` : ""}
              </p>
            </div>
          </section>

          <section className={LEARNING_SECTION_CLASS}>
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
        </>
      )}
    </LearningDialog>
  );
}
