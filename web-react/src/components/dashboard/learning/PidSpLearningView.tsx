import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { fetchPidSpLearningReport } from "../../../helpers/pidSpLearning/pidSpLearningApi";
import type {
  PidSpGateValue,
  PidSpLearningGate,
  PidSpModel,
} from "../../../helpers/pidSpLearning/types";
import { LearningDialog } from "./LearningDialog";
import { LEARNING_SECTION_CLASS } from "./learningDisplay";

const REPORT_REFRESH_MS = 5_000;
const REPORT_QUERY_ROOT = "learning-report";
const GATE_LABELS: Record<string, string> = {
  accepted_samples: "Accepted samples",
  accepted_duration: "Accepted duration",
  duty_standard_deviation: "Duty standard deviation",
  duty_transition: "Duty transition",
  temperature_span: "Temperature span",
};

export interface PidSpLearningViewProps {
  apiBase: string;
  selectedController: string | null;
  /** Raw socket invalidation digest. REST remains the rendered authority. */
  modelLearningRevision?: string | null;
}

class PidSpReportRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PidSpReportRequestError";
  }
}

function yesNo(value: boolean): string {
  return value ? "yes" : "no";
}

function gateValue(value: PidSpGateValue, unit: string | null): string {
  const shown = typeof value === "boolean" ? yesNo(value) : String(value);
  return unit === null ? shown : `${shown} ${unit}`;
}

function GateTable({ gates }: { gates: PidSpLearningGate[] }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="text-probe-label">
          <tr>
            <th className="border-b border-card-border p-2" scope="col">
              Gate
            </th>
            <th className="border-b border-card-border p-2" scope="col">
              Result
            </th>
            <th className="border-b border-card-border p-2" scope="col">
              Observed
            </th>
            <th className="border-b border-card-border p-2" scope="col">
              Required
            </th>
          </tr>
        </thead>
        <tbody>
          {gates.map((gate) => (
            <tr key={gate.name}>
              <th className="border-b border-card-border p-2 font-semibold" scope="row">
                {GATE_LABELS[gate.name] ?? gate.name.replaceAll("_", " ")}
              </th>
              <td
                className={`border-b border-card-border p-2 font-semibold ${gate.passed ? "text-ok" : "text-danger"}`}
              >
                {gate.passed ? "Met" : "Not met"}
              </td>
              <td className="border-b border-card-border p-2">
                {gateValue(gate.observed, gate.unit)}
              </td>
              <td className="border-b border-card-border p-2">
                {gateValue(gate.required, gate.unit)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface ModelTableProps {
  heading: string;
  model: PidSpModel;
}

function ModelTable({ heading, model }: ModelTableProps) {
  const rows =
    model.form === "fopdt"
      ? [
          {
            name: "K",
            value: model.K,
            unit: "°F per duty ratio",
            meaning: "Steady-state temperature gain",
          },
          {
            name: "tau",
            value: model.tau,
            unit: "seconds",
            meaning: "Process time constant",
          },
          {
            name: "theta",
            value: model.theta,
            unit: "seconds",
            meaning: "Transport delay",
          },
        ]
      : [
          {
            name: "K_i",
            value: model.K_i,
            unit: "°F/s per duty ratio",
            meaning: "Integrating temperature gain",
          },
          {
            name: "c0",
            value: model.c0,
            unit: "°F/s",
            meaning: "Integrating drift offset",
          },
          {
            name: "theta",
            value: model.theta,
            unit: "seconds",
            meaning: "Transport delay",
          },
        ];

  return (
    <section className={LEARNING_SECTION_CLASS}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-bold">{heading}</h3>
        <p className="text-sm uppercase tracking-wide text-probe-label">{model.form}</p>
      </div>
      <p className="mt-2 text-sm text-probe-label">Revision {model.revision}</p>
      {model.identified_at_f !== undefined && (
        <p className="mt-1 text-sm text-probe-label">Identified at: {model.identified_at_f} °F</p>
      )}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="text-probe-label">
            <tr>
              <th className="border-b border-card-border p-2" scope="col">
                Parameter
              </th>
              <th className="border-b border-card-border p-2" scope="col">
                Value
              </th>
              <th className="border-b border-card-border p-2" scope="col">
                Unit
              </th>
              <th className="border-b border-card-border p-2" scope="col">
                Meaning
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <th className="border-b border-card-border p-2 font-mono" scope="row">
                  {row.name}
                </th>
                <td className="border-b border-card-border p-2 tabular-nums">{row.value}</td>
                <td className="border-b border-card-border p-2">{row.unit}</td>
                <td className="border-b border-card-border p-2 text-probe-label">{row.meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function PidSpLearningView(props: PidSpLearningViewProps) {
  return props.selectedController === "pid_sp" ? <ActivePidSpLearningView {...props} /> : null;
}

function ActivePidSpLearningView({ apiBase, modelLearningRevision }: PidSpLearningViewProps) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(() => [REPORT_QUERY_ROOT, "pid_sp", apiBase] as const, [apiBase]);
  const requestGeneration = useRef(0);
  const lastModelLearningRevision = useRef(modelLearningRevision);
  const {
    data: report,
    error: reportQueryError,
    isPending,
    refetch,
  } = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const generation = ++requestGeneration.current;
      const result = await fetchPidSpLearningReport(apiBase, signal);
      if (signal.aborted || generation !== requestGeneration.current) {
        throw new DOMException("Superseded PID-SP learning report request", "AbortError");
      }
      if (!result.ok || result.data === null) {
        throw new PidSpReportRequestError(result.message || "PID-SP learning report unavailable");
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
        : "PID-SP learning report unavailable";

  const refreshReport = useCallback(async () => {
    requestGeneration.current += 1;
    await refetch({ cancelRefetch: true });
  }, [refetch]);

  useEffect(() => {
    if (lastModelLearningRevision.current === modelLearningRevision) return;
    lastModelLearningRevision.current = modelLearningRevision;
    requestGeneration.current += 1;
    void queryClient
      .cancelQueries({ queryKey, exact: true })
      .then(() => queryClient.invalidateQueries({ queryKey, exact: true }));
  }, [modelLearningRevision, queryClient, queryKey]);

  const dialogStatus =
    reportError !== null
      ? "error"
      : isPending && report === undefined
        ? "loading"
        : (report?.status ?? "unavailable");

  return (
    <LearningDialog
      controllerLabel="PID-SP"
      title="PID-SP model learning"
      closeLabel="Close PID-SP model learning"
      status={dialogStatus}
      loading={isPending && report === undefined}
      loadingLabel="Loading PID-SP learning report…"
      error={reportError}
      retryLabel="Retry PID-SP learning report"
      onRetry={refreshReport}
    >
      {report && (
        <>
          {report.failure && (
            <div
              className="grid gap-1 rounded-lg border border-danger p-3 text-danger"
              role="alert"
            >
              <p className="font-semibold">{report.failure.code}</p>
              <p>{report.failure.detail}</p>
              <p className="text-sm">{report.failure.terminal ? "terminal" : "recoverable"}</p>
            </div>
          )}

          {!report.live && report.checkpoint === null && report.failure === null && (
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">No PID-SP learning data is available yet.</h3>
              <p className="mt-2 text-sm text-probe-label">
                Diagnostics are collected automatically while PID-SP Hold is running.
              </p>
            </section>
          )}

          {report.checkpoint && (
            <ModelTable heading="Durable checkpoint" model={report.checkpoint} />
          )}

          {report.live && report.gates.length > 0 && (
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Excitation gates</h3>
              <p className="mt-2 text-sm text-probe-label">
                Results and thresholds are reported by the controller.
              </p>
              <GateTable gates={report.gates} />
            </section>
          )}

          {report.identifier && (
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Identifier diagnostics</h3>
              <dl className="mt-3 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-probe-label">Accepted samples</dt>
                  <dd>Accepted samples: {report.identifier.accepted}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Accepted time</dt>
                  <dd>Accepted time: {report.identifier.accepted_seconds} seconds</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Duty variation</dt>
                  <dd>Duty variation: {report.identifier.duty_std}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Temperature span</dt>
                  <dd>Temperature span: {report.identifier.temp_span} °F</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Transition</dt>
                  <dd>Transition observed: {yesNo(report.identifier.transition_seen)}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Duty segments</dt>
                  <dd>Duty segments: {report.identifier.duty_segments}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Candidate count</dt>
                  <dd>Candidates passing: {report.identifier.candidates_passing}</dd>
                </div>
                {report.confirmation && (
                  <div>
                    <dt className="text-probe-label">Confirmation progress</dt>
                    <dd>
                      Confirmation progress: {report.confirmation.observed ?? "not started"} of{" "}
                      {report.confirmation.required}
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-probe-label">Best residual</dt>
                  <dd>Best residual: {report.identifier.best_residual}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Runner-up residual</dt>
                  <dd>Runner-up residual: {report.identifier.runner_up_residual}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Distrust count</dt>
                  <dd>Distrust count: {report.identifier.distrust_count}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Distrust ratio</dt>
                  <dd>Distrust ratio: {report.identifier.distrust_ratio}</dd>
                </div>
              </dl>
              <p className="mt-3 text-sm text-probe-label">
                Trusted model:{" "}
                {report.identifier.trusted === null
                  ? "none"
                  : `${report.identifier.trusted.form} revision ${report.identifier.trusted.revision}`}
              </p>
            </section>
          )}

          {report.predictor && (
            <section className={LEARNING_SECTION_CLASS}>
              <h3 className="font-bold">Predictor diagnostics</h3>
              <dl className="mt-3 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-probe-label">Active</dt>
                  <dd>Active: {yesNo(report.predictor.active)}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Disabled</dt>
                  <dd>Disabled: {yesNo(report.predictor.disabled)}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Residual streak</dt>
                  <dd>Residual streak: {report.predictor.residual_streak}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">Truncation count</dt>
                  <dd>Truncation count: {report.predictor.truncated}</dd>
                </div>
                <div>
                  <dt className="text-probe-label">x0</dt>
                  <dd>x0: {report.predictor.x0} °F</dd>
                </div>
                <div>
                  <dt className="text-probe-label">xd</dt>
                  <dd>xd: {report.predictor.xd} °F</dd>
                </div>
              </dl>
              <p className="mt-3 text-sm text-probe-label">
                Predictor model:{" "}
                {report.predictor.model === null ? "none" : report.predictor.model.form}
              </p>
            </section>
          )}

          <p className="break-all font-mono text-xs text-probe-label">
            Report revision: {report.revision}
          </p>
        </>
      )}
    </LearningDialog>
  );
}
