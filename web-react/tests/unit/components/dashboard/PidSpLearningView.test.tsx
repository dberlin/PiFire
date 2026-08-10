import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PidSpLearningView } from "../../../../src/components/dashboard/learning/PidSpLearningView";
import type {
  PidSpCheckpointModel,
  PidSpLearningReport,
  PidSpLearningStatus,
  PidSpPredictorModel,
} from "../../../../src/helpers/contracts/learning.gen";
import { testQueryClient } from "../../test-utils";

const FOPDT: PidSpCheckpointModel = {
  form: "fopdt",
  K: 86.5,
  tau: 610,
  theta: 45,
  revision: 7,
  identified_at_f: 250,
};

const PREDICTOR_FOPDT: PidSpPredictorModel = {
  form: "fopdt",
  K: 86.5,
  tau: 610,
  theta: 45,
};

const REPORT: PidSpLearningReport = {
  schema_version: 1,
  controller: "pid_sp",
  status: "evaluating",
  live: true,
  revision: "a".repeat(64),
  gates: [
    {
      name: "accepted_samples",
      passed: false,
      observed: 200,
      required: 120,
      unit: "samples",
    },
    {
      name: "accepted_duration",
      passed: true,
      observed: 720.5,
      required: 600,
      unit: "seconds",
    },
    {
      name: "duty_standard_deviation",
      passed: true,
      observed: 0.082,
      required: 0.06,
      unit: "ratio",
    },
    {
      name: "duty_transition",
      passed: false,
      observed: true,
      required: true,
      unit: null,
    },
    {
      name: "temperature_span",
      passed: true,
      observed: 18.25,
      required: 15,
      unit: "°F",
    },
  ],
  confirmation: { observed: 3, required: 4 },
  identifier: {
    accepted: 144,
    accepted_seconds: 720.5,
    duty_std: 0.082,
    temp_span: 18.25,
    transition_seen: true,
    duty_segments: 6,
    best_residual: 0.42,
    runner_up_residual: 0.73,
    candidates_passing: 2,
    confirming: 3,
    trusted: FOPDT,
    distrust_count: 1,
    distrust_ratio: 0.125,
  },
  predictor: {
    active: true,
    disabled: false,
    x0: 241.25,
    xd: 238.75,
    residual_streak: 1,
    truncated: 2,
    model: PREDICTOR_FOPDT,
  },
  checkpoint: FOPDT,
  failure: null,
};

const IDLE: PidSpLearningReport = {
  schema_version: 1,
  controller: "pid_sp",
  status: "idle",
  live: false,
  revision: "b".repeat(64),
  gates: [],
  confirmation: null,
  identifier: null,
  predictor: null,
  checkpoint: null,
  failure: null,
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type FetchMock = Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>;
let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = rs.fn(async () => jsonResponse(REPORT));
  rs.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  rs.unstubAllGlobals();
  rs.useRealTimers();
});

function renderPanel(props: Partial<React.ComponentProps<typeof PidSpLearningView>> = {}) {
  const queryClient = testQueryClient();
  return render(
    <PidSpLearningView
      apiBase=""
      selectedController="pid_sp"
      modelLearningRevision="socket-a"
      {...props}
    />,
    {
      wrapper: ({ children }: React.PropsWithChildren) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    },
  );
}

async function openPanel() {
  await userEvent.click(await screen.findByRole("button", { name: /PID-SP learning:/i }));
  return screen.findByRole("dialog", { name: "PID-SP model learning" });
}

function reportForStatus(status: PidSpLearningStatus): PidSpLearningReport {
  if (status === "idle") return IDLE;
  if (status === "error") {
    return {
      ...IDLE,
      status: "error",
      checkpoint: FOPDT,
      failure: {
        code: "live-status-invalid",
        detail: "identifier.accepted must be a number",
        terminal: false,
      },
    };
  }
  return { ...REPORT, status };
}

describe("PidSpLearningView", () => {
  it("renders nothing and makes no request for another controller", () => {
    renderPanel({ selectedController: "mpc" });

    expect(screen.queryByRole("button", { name: /PID-SP learning:/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["idle", "Idle", "text-probe-label"],
    ["collecting", "Collecting", "text-probe-label"],
    ["insufficient-excitation", "Insufficient excitation", "text-warn"],
    ["evaluating", "Evaluating", "text-accent"],
    ["active", "Active", "text-ok"],
    ["fallback", "Fallback", "text-warn"],
    ["error", "Error", "text-danger"],
  ] as const)(
    "normalizes the %s status in the pill and uses its shared tone",
    async (status, label, tone) => {
      fetchMock.mockResolvedValue(jsonResponse(reportForStatus(status)));
      renderPanel();

      const trigger = await screen.findByRole("button", {
        name: `PID-SP learning: ${label.toLowerCase()}`,
      });
      await userEvent.click(trigger);

      expect(screen.getByText(label, { exact: true, selector: "header p" })).toHaveClass(tone);
    },
  );

  it("shows a useful empty state when neither live data nor a checkpoint exists", async () => {
    fetchMock.mockResolvedValue(jsonResponse(IDLE));
    renderPanel();
    const dialog = await openPanel();

    expect(dialog).toHaveTextContent("No PID-SP learning data is available yet.");
    expect(dialog).toHaveTextContent(
      "Diagnostics are collected automatically while PID-SP Hold is running.",
    );
    expect(
      within(dialog).queryByRole("heading", { name: "Durable checkpoint" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    [
      "fopdt",
      FOPDT,
      [
        ["K", "86.5", "°F per duty ratio"],
        ["tau", "610", "seconds"],
        ["theta", "45", "seconds"],
      ],
    ],
    [
      "ipdt",
      {
        form: "ipdt",
        K_i: 0.87,
        c0: -0.18,
        theta: 30,
        revision: 9,
        identified_at_f: 275,
      } satisfies PidSpCheckpointModel,
      [
        ["K_i", "0.87", "°F/s per duty ratio"],
        ["c0", "-0.18", "°F/s"],
        ["theta", "30", "seconds"],
      ],
    ],
  ] as const)("renders the durable %s checkpoint parameters", async (_form, model, rows) => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ...IDLE, checkpoint: model, revision: "c".repeat(64) }),
    );
    renderPanel();
    const dialog = await openPanel();
    const section = within(dialog)
      .getByRole("heading", { name: "Durable checkpoint" })
      .closest("section");
    expect(section).not.toBeNull();

    for (const [name, value, unit] of rows) {
      expect(
        within(section!).getByRole("row", {
          name: new RegExp(`^${name} ${value} .*${unit}`),
        }),
      ).toBeVisible();
    }
    expect(section!).toHaveTextContent(_form === "ipdt" ? "Revision 9" : "Revision 7", {
      normalizeWhitespace: true,
    });
    expect(section!).toHaveTextContent(
      _form === "ipdt" ? "Identified at: 275 °F" : "Identified at: 250 °F",
      { normalizeWhitespace: true },
    );
  });

  it("displays backend gate decisions and thresholds without rederiving pass or fail", async () => {
    renderPanel();
    const dialog = await openPanel();
    const section = within(dialog)
      .getByRole("heading", { name: "Excitation gates" })
      .closest("section");
    expect(section).not.toBeNull();

    expect(
      within(section!).getByRole("row", {
        name: "Accepted samples Not met 200 samples 120 samples",
      }),
    ).toBeVisible();
    expect(
      within(section!).getByRole("row", {
        name: "Duty transition Not met yes yes",
      }),
    ).toBeVisible();
    expect(
      within(section!).getByRole("row", {
        name: "Accepted duration Met 720.5 seconds 600 seconds",
      }),
    ).toBeVisible();
  });

  it("renders all identifier, confirmation, fit, distrust, and predictor diagnostics", async () => {
    renderPanel();
    const dialog = await openPanel();

    const identifier = within(dialog)
      .getByRole("heading", { name: "Identifier diagnostics" })
      .closest("section");
    expect(identifier).not.toBeNull();
    for (const detail of [
      "Accepted samples: 144",
      "Accepted time: 720.5 seconds",
      "Duty variation: 0.082",
      "Temperature span: 18.25 °F",
      "Transition observed: yes",
      "Duty segments: 6",
      "Candidates passing: 2",
      "Best residual: 0.42",
      "Runner-up residual: 0.73",
      "Distrust count: 1",
      "Distrust ratio: 0.125",
    ]) {
      expect(identifier!).toHaveTextContent(detail);
    }
    expect(dialog).toHaveTextContent("Confirmation progress: 3 of 4");

    const predictor = within(dialog)
      .getByRole("heading", { name: "Predictor diagnostics" })
      .closest("section");
    expect(predictor).not.toBeNull();
    for (const detail of [
      "Active: yes",
      "Disabled: no",
      "Residual streak: 1",
      "Truncation count: 2",
      "x0: 241.25 °F",
      "xd: 238.75 °F",
    ]) {
      expect(predictor!).toHaveTextContent(detail);
    }
    expect(predictor!).toHaveTextContent("Predictor model: fopdt");
    expect(predictor!).not.toHaveTextContent("revision");
  });

  it("keeps a durable checkpoint visible beside a structured live failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse(reportForStatus("error")));
    renderPanel();
    const dialog = await openPanel();

    const alert = within(dialog).getByRole("alert");
    expect(alert).toHaveTextContent("live-status-invalid");
    expect(alert).toHaveTextContent("identifier.accepted must be a number");
    expect(alert).toHaveTextContent("recoverable");
    expect(within(dialog).getByRole("heading", { name: "Durable checkpoint" })).toBeVisible();
  });

  it("shows an explicit loading state while the first report is pending", async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    renderPanel();

    const trigger = screen.getByRole("button", {
      name: "PID-SP learning: loading",
    });
    await userEvent.click(trigger);
    expect(screen.getByText("Loading PID-SP learning report…")).toBeVisible();
  });

  it("exposes no mutation controls or mutation requests after loading", async () => {
    renderPanel();
    const dialog = await openPanel();

    expect(within(dialog).getAllByRole("button")).toHaveLength(1);
    for (const name of [
      /calibrat/i,
      /activat/i,
      /roll back/i,
      /reset/i,
      /start learning/i,
      /pause/i,
    ]) {
      expect(within(dialog).queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("GET");
  });

  it("keeps the previous report visible on refresh failure and retries in place", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(REPORT))
      .mockResolvedValueOnce(jsonResponse({ detail: "report temporarily unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse({ ...REPORT, status: "active" }));
    const view = renderPanel({ modelLearningRevision: "socket-a" });
    await openPanel();
    expect(screen.getByText("86.5", { exact: true })).toBeVisible();

    view.rerender(
      <PidSpLearningView apiBase="" selectedController="pid_sp" modelLearningRevision="socket-b" />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("report temporarily unavailable");
    expect(screen.getByText("86.5", { exact: true })).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Retry PID-SP learning report" }));
    expect(await screen.findByRole("button", { name: "PID-SP learning: active" })).toBeVisible();
  });

  it("polls every five seconds and recovers from an initial failure", async () => {
    rs.useFakeTimers();
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "not ready" }, 503))
      .mockResolvedValueOnce(jsonResponse(REPORT));
    renderPanel();
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("button", { name: "PID-SP learning: error" })).toBeVisible();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(5_000);
      await rs.advanceTimersByTimeAsync(1);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "PID-SP learning: evaluating" })).toBeVisible();
  });

  it("invalidates on the raw socket revision and fences the superseded response", async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    fetchMock
      .mockImplementationOnce(
        (_input, init) =>
          new Promise<Response>((resolve, reject) => {
            resolveFirst = resolve;
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ...REPORT, status: "active", revision: "d".repeat(64) }),
      );
    const view = renderPanel({ modelLearningRevision: "wire-old" });

    view.rerender(
      <PidSpLearningView apiBase="" selectedController="pid_sp" modelLearningRevision="wire-new" />,
    );
    expect(await screen.findByRole("button", { name: "PID-SP learning: active" })).toBeVisible();

    await act(async () => {
      resolveFirst?.(
        jsonResponse({
          ...REPORT,
          status: "collecting",
          revision: "e".repeat(64),
        }),
      );
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "PID-SP learning: active" })).toBeVisible();
  });

  it("fences a late response when the API base changes", async () => {
    let oldSignal: AbortSignal | null | undefined;
    let resolveOld: ((response: Response) => void) | undefined;
    fetchMock.mockImplementation((input, init) => {
      if (String(input).startsWith("https://old.example")) {
        oldSignal = init?.signal;
        return new Promise<Response>((resolve) => {
          resolveOld = resolve;
        });
      }
      return Promise.resolve(
        jsonResponse({ ...REPORT, status: "active", revision: "f".repeat(64) }),
      );
    });
    const view = renderPanel({ apiBase: "https://old.example" });

    view.rerender(
      <PidSpLearningView
        apiBase="https://new.example"
        selectedController="pid_sp"
        modelLearningRevision="socket-a"
      />,
    );
    expect(await screen.findByRole("button", { name: "PID-SP learning: active" })).toBeVisible();
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOld?.(
        jsonResponse({
          ...REPORT,
          status: "collecting",
          revision: "1".repeat(64),
        }),
      );
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "PID-SP learning: active" })).toBeVisible();
  });

  it("aborts the report request when the controller changes or the view unmounts", async () => {
    const signals: AbortSignal[] = [];
    fetchMock.mockImplementation(
      (_input, init) =>
        new Promise<Response>(() => {
          if (init?.signal) signals.push(init.signal);
        }),
    );
    const view = renderPanel();
    await act(async () => {
      await Promise.resolve();
    });

    view.rerender(
      <PidSpLearningView apiBase="" selectedController="mpc" modelLearningRevision="socket-a" />,
    );
    expect(signals[0]?.aborted).toBe(true);

    view.rerender(
      <PidSpLearningView apiBase="" selectedController="pid_sp" modelLearningRevision={null} />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    view.unmount();
    expect(signals[1]?.aborted).toBe(true);
  });
});
