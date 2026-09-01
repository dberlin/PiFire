import type {
  ModelEvidenceReport,
  ModelEvidenceStatus,
  PidSpLearningReport,
} from "@pifire/core/contracts/learning";
import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import { LearningPanel } from "../../../../src/components/dashboard/LearningPanel";
import { testQueryClient } from "../../test-utils";

function mpcReport(status: ModelEvidenceStatus, revision: string): ModelEvidenceReport {
  return {
    schema_version: 3,
    status,
    mode: "passive-online",
    decision_id: `decision-${revision}`,
    evidence: {
      count: 0,
      audit_count: 0,
      high_water: null,
      retired_excluded: 0,
    },
    fit: { status: "idle", request_id: null, fit_corpus_digest: null, error: null },
    checks: {},
    candidate: null,
    evaluation: null,
    corpus: {
      digest: null,
      revision: null,
      fit_partition_digest: null,
      slices: [],
    },
    activation: {
      phase: status === "active" ? "active" : "aborted",
      origin: "passive-online",
      policy: "causal-auto",
      reason: null,
      pending_persistence: false,
      pending_frame_boundary_swap: false,
    },
    active_model: {
      digest: "a".repeat(64),
      role_generation: 1,
    },
    identities: {
      active_digest: null,
      active_generation: null,
      candidate_digest: null,
      candidate_generation: null,
      rollback_digest: null,
      rollback_generation: null,
    },
    calibration: { revision: 0, command_high_water: 0 },
    latest_lifecycle: null,
    failure: null,
    gates: [],
    blockers: [],
    errors: [],
    revision,
  };
}

function pidSpReport(revision: string): PidSpLearningReport {
  return {
    schema_version: 1,
    controller: "pid_sp",
    status: "idle",
    live: false,
    revision,
    gates: [],
    confirmation: null,
    identifier: null,
    predictor: null,
    checkpoint: null,
    comparison: null,
    active_model: null,
    delay_evidence: null,
    failure: null,
  };
}

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

type FetchMock = Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>;
let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = rs.fn(async (input) => {
    if (String(input).endsWith("/api/model-evidence/report")) {
      return jsonResponse(mpcReport("collecting", "a".repeat(64)));
    }
    if (String(input).endsWith("/api/pid-sp-learning/report")) {
      return jsonResponse(pidSpReport("b".repeat(64)));
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  rs.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  rs.unstubAllGlobals();
});

const panel = (
  selectedController: string | null,
  overrides: Partial<React.ComponentProps<typeof LearningPanel>> = {},
) => (
  <LearningPanel
    apiBase=""
    selectedController={selectedController}
    units="F"
    ambientC={20}
    modelLearningRevision="wire-a"
    {...overrides}
    currentMode="Hold"
    displayMode="Hold"
    criticalError={false}
  />
);

function renderPanel(
  selectedController: string | null,
  overrides: Partial<React.ComponentProps<typeof LearningPanel>> = {},
) {
  const queryClient = testQueryClient();
  return render(panel(selectedController, overrides), {
    wrapper: ({ children }: React.PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

const requestsTo = (suffix: string) =>
  fetchMock.mock.calls.filter(([input]) => String(input).endsWith(suffix));

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("LearningPanel", () => {
  it.each(["pid", "unknown", null])(
    "renders nothing and makes no report request for %s",
    async (selectedController) => {
      const view = renderPanel(selectedController);
      await act(async () => Promise.resolve());

      expect(view.container).toBeEmptyDOMElement();
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("mounts and requests only the MPC provider for exact mpc", async () => {
    renderPanel("mpc");

    expect(await screen.findByRole("button", { name: "MPC learning: collecting" })).toBeVisible();
    expect(requestsTo("/api/model-evidence/report")).toHaveLength(1);
    expect(requestsTo("/api/pid-sp-learning/report")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /PID-SP learning:/i })).not.toBeInTheDocument();
  });

  it("mounts and requests only the PID-SP provider for exact pid_sp", async () => {
    renderPanel("pid_sp");

    expect(await screen.findByRole("button", { name: "PID-SP learning: idle" })).toBeVisible();
    expect(requestsTo("/api/pid-sp-learning/report")).toHaveLength(1);
    expect(requestsTo("/api/model-evidence/report")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /MPC learning:/i })).not.toBeInTheDocument();
  });

  it("fences a late MPC response after handing off to PID-SP", async () => {
    const oldMpc = deferredResponse();
    fetchMock.mockImplementation((input) => {
      if (String(input).endsWith("/api/model-evidence/report")) return oldMpc.promise;
      return Promise.resolve(jsonResponse(pidSpReport("c".repeat(64))));
    });
    const view = renderPanel("mpc");

    expect(screen.getByRole("button", { name: "MPC learning: loading" })).toBeVisible();
    view.rerender(panel("pid_sp"));
    expect(await screen.findByRole("button", { name: "PID-SP learning: idle" })).toBeVisible();

    await act(async () => {
      oldMpc.resolve(jsonResponse(mpcReport("collecting", "c".repeat(64))));
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "PID-SP learning: idle" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /MPC learning:/i })).not.toBeInTheDocument();
  });

  it("fences a late PID-SP response after handing off to MPC", async () => {
    const oldPidSp = deferredResponse();
    fetchMock.mockImplementation((input) => {
      if (String(input).endsWith("/api/pid-sp-learning/report")) return oldPidSp.promise;
      return Promise.resolve(jsonResponse(mpcReport("active", "d".repeat(64))));
    });
    const view = renderPanel("pid_sp");

    expect(screen.getByRole("button", { name: "PID-SP learning: loading" })).toBeVisible();
    view.rerender(panel("mpc"));
    expect(await screen.findByRole("button", { name: "MPC learning: active" })).toBeVisible();

    await act(async () => {
      oldPidSp.resolve(jsonResponse(pidSpReport("d".repeat(64))));
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "MPC learning: active" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /PID-SP learning:/i })).not.toBeInTheDocument();
  });

  it("does not show the prior API base report while the new base is pending", async () => {
    const newBase = deferredResponse();
    fetchMock.mockImplementation((input) => {
      if (String(input).startsWith("/new")) return newBase.promise;
      return Promise.resolve(jsonResponse(mpcReport("active", "e".repeat(64))));
    });
    const view = renderPanel("mpc", { apiBase: "/old" });
    expect(await screen.findByRole("button", { name: "MPC learning: active" })).toBeVisible();

    view.rerender(panel("mpc", { apiBase: "/new" }));
    expect(screen.getByRole("button", { name: "MPC learning: loading" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "MPC learning: active" })).not.toBeInTheDocument();
  });

  it("does not resurrect a successful report after an inactive remount", async () => {
    const remount = deferredResponse();
    let mpcRequests = 0;
    fetchMock.mockImplementation((input) => {
      if (!String(input).endsWith("/api/model-evidence/report")) {
        return Promise.resolve(jsonResponse(pidSpReport("e".repeat(64))));
      }
      mpcRequests += 1;
      return mpcRequests === 1
        ? Promise.resolve(jsonResponse(mpcReport("active", "f".repeat(64))))
        : remount.promise;
    });
    const view = renderPanel("mpc");
    expect(await screen.findByRole("button", { name: "MPC learning: active" })).toBeVisible();

    view.rerender(panel("pid"));
    await act(async () => Promise.resolve());
    view.rerender(panel("mpc"));

    expect(screen.getByRole("button", { name: "MPC learning: loading" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "MPC learning: active" })).not.toBeInTheDocument();
    expect(mpcRequests).toBe(2);
  });

  it("uses revision changes to invalidate only the active provider query", async () => {
    const view = renderPanel("mpc");
    await screen.findByRole("button", { name: "MPC learning: collecting" });

    view.rerender(panel("mpc", { modelLearningRevision: "wire-b" }));
    await waitFor(() => expect(requestsTo("/api/model-evidence/report")).toHaveLength(2));
    expect(requestsTo("/api/pid-sp-learning/report")).toHaveLength(0);

    view.rerender(panel("pid_sp", { modelLearningRevision: "wire-b" }));
    await screen.findByRole("button", { name: "PID-SP learning: idle" });
    expect(requestsTo("/api/model-evidence/report")).toHaveLength(2);
    expect(requestsTo("/api/pid-sp-learning/report")).toHaveLength(1);

    view.rerender(panel("pid_sp", { modelLearningRevision: "wire-c" }));
    await waitFor(() => expect(requestsTo("/api/pid-sp-learning/report")).toHaveLength(2));
    expect(requestsTo("/api/model-evidence/report")).toHaveLength(2);
  });
});
