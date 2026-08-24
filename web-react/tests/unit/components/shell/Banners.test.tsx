import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import {
  projectProbeHealth,
  summarizeProbeHealth,
} from "@pifire/core/dashboard/probeHealth";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Banners } from "../../../../src/components/shell/Banners";
import * as warningsApi from "../../../../src/helpers/shell/warningsApi";

rs.mock("../../../../src/helpers/shell/warningsApi", () => ({ dismissWarnings: rs.fn() }));

afterEach(() => {
  rs.resetAllMocks();
  cleanup();
});

function health(
  displayName: string,
  outcome: ThermocoupleHealthView["outcome"],
  current = true,
): ThermocoupleHealthView {
  return {
    device: `${displayName} device`,
    port: "KTT0",
    label: displayName,
    displayName,
    role: outcome === "stopped" || outcome === "notify_only" ? "Primary" : "Food",
    report: {
      state: "confirmed",
      faults: ["open"],
      evidence: ["hardware"],
      temperatureValid: outcome === "notify_only",
      detail: {},
    },
    detector: { source: "hardware", policy: "observe" },
    outcome,
    freshness: { current, lastReportedAgeS: current ? 0 : 42 },
  };
}

function summary(...items: ThermocoupleHealthView[]) {
  const result = summarizeProbeHealth(items.map(projectProbeHealth));
  if (result === null) throw new Error("health fixture must contain an active issue");
  return result;
}

describe("Banners", () => {
  it("renders one banner per error and warning", () => {
    render(
      <Banners
        errors={["control down"]}
        warnings={["lid open", "low hopper"]}
        warningsMaxId={1}
        criticalError={false}
      />,
    );
    expect(screen.getByText("control down")).toBeInTheDocument();
    expect(screen.getByText("lid open")).toBeInTheDocument();
    expect(screen.getByText("low hopper")).toBeInTheDocument();
  });

  it("styles error banners as plain error by default, not critical", () => {
    render(
      <Banners
        errors={["control down"]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={false}
      />,
    );
    expect(screen.getByText("control down")).toHaveClass("pf-banner--error");
    expect(screen.getByText("control down")).not.toHaveClass("pf-banner--critical");
  });

  it("styles the error banner critical when criticalError is set", () => {
    render(
      <Banners
        errors={["high limit shutdown"]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={true}
      />,
    );
    expect(screen.getByText("high limit shutdown")).toHaveClass("pf-banner--critical");
  });

  it("renders nothing when there are no errors or warnings", () => {
    const { container } = render(
      <Banners errors={[]} warnings={[]} warningsMaxId={null} criticalError={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows no dismiss control when there are no warnings", () => {
    render(<Banners errors={["boom"]} warnings={[]} warningsMaxId={null} criticalError={false} />);
    expect(screen.queryByRole("button", { name: /dismiss warnings/i })).toBeNull();
  });

  it("shows a warning with no id and offers no dismiss control", () => {
    // The backend only reports a null max id when there are no warnings, so this
    // payload is impossible today. If that invariant ever breaks, the warning
    // must surface undismissable rather than be silently swallowed.
    render(
      <Banners errors={[]} warnings={["orphan"]} warningsMaxId={null} criticalError={false} />,
    );
    expect(screen.getByText("orphan")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /dismiss warnings/i })).toBeNull();
  });

  it("offers no dismiss control when a max id arrives with no warnings", () => {
    // The backend's single-query snapshot couples max_id === null to
    // warnings === [], so {warnings: [], warningsMaxId: 5} is unreachable
    // today. Guard it anyway: the dismiss button must never appear with
    // nothing behind it, since clicking it would clear rows the user never
    // saw.
    render(<Banners errors={["boom"]} warnings={[]} warningsMaxId={5} criticalError={false} />);
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /dismiss warnings/i })).toBeNull();
  });

  it("posts the high-water mark and hides the warnings on dismiss", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(true);
    render(
      <Banners errors={[]} warnings={["hopper low"]} warningsMaxId={5} criticalError={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.queryByText("hopper low")).toBeNull());
    expect(warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).toHaveBeenCalledWith(5);
  });

  it("keeps the warnings up when the dismiss is refused", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(false);
    render(
      <Banners errors={[]} warnings={["hopper low"]} warningsMaxId={5} criticalError={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    // Wait for the call, then let the click handler's continuation and any state
    // update it queues run. Asserting before that flush would only re-check the
    // pre-click DOM and would pass even if the refusal were ignored.
    //
    // The single `act(async () => { await Promise.resolve(); })` below flushes
    // exactly one microtask turn past the mock call, which is only enough if
    // `onDismiss` has exactly one `await` between calling dismissWarnings and
    // deciding whether to update state. If onDismiss changes, re-verify this
    // test's power by temporarily mutating its production line to
    // `await dismissWarnings(warningsMaxId); setDismissedThroughId(warningsMaxId);`
    // (an extra await) — this test MUST fail under that mutant.
    await waitFor(() =>
      expect(warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).toHaveBeenCalledWith(5),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("hopper low")).toBeTruthy();
    expect(screen.getByRole("button", { name: /dismiss warnings/i })).toBeTruthy();
  });

  it("shows a newer warning that arrives after a dismiss", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(true);
    const { rerender } = render(
      <Banners errors={[]} warnings={["hopper low"]} warningsMaxId={5} criticalError={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.queryByText("hopper low")).toBeNull());
    // A higher mark means the backend raised something new -- it must not be
    // swallowed by the earlier dismiss.
    rerender(
      <Banners errors={[]} warnings={["auger jam"]} warningsMaxId={6} criticalError={false} />,
    );
    expect(screen.getByText("auger jam")).toBeTruthy();
  });

  it("still renders errors after warnings are dismissed", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(true);
    render(
      <Banners
        errors={["boom"]}
        warnings={["hopper low"]}
        warningsMaxId={5}
        criticalError={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.queryByText("hopper low")).toBeNull());
    expect(screen.getByText("boom")).toBeTruthy();
  });

  it("renders confirmed health as a structured non-dismissible danger banner", () => {
    render(
      <Banners
        errors={[]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={false}
        probeHealthSummary={summary(health("Grill", "notify_only"))}
      />,
    );

    const banner = screen.getByRole("alert");
    expect(banner).toHaveClass("pf-banner--critical");
    expect(banner).toHaveTextContent("FAULT");
    expect(banner).toHaveTextContent("Grill");
    expect(banner).toHaveTextContent("Fault detected — Observe mode did not stop heating.");
    expect(banner).toHaveTextContent("Hardware reported an open circuit.");
    expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
  });

  it("summarizes additional confirmed probes without adding dismiss controls", () => {
    render(
      <Banners
        errors={[]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={false}
        probeHealthSummary={summary(
          health("Grill", "stopped"),
          health("Brisket", "unavailable"),
          health("Ambient", "unavailable"),
        )}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("CONTROL PROBE UNAVAILABLE");
    expect(screen.getByRole("alert")).toHaveTextContent("+2 more");
    expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
  });

  it("qualifies retained health when the live transport is stale", () => {
    render(
      <Banners
        errors={[]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={false}
        probeHealthSummary={summary(health("Grill", "stopped"))}
        healthLastReported
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Last reported: CONTROL PROBE UNAVAILABLE");
  });

  it("removes current health treatment immediately after recovery", () => {
    const props = {
      errors: [],
      warnings: [],
      warningsMaxId: null,
      criticalError: false,
    };
    const { rerender } = render(
      <Banners {...props} probeHealthSummary={summary(health("Grill", "stopped"))} />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();

    rerender(<Banners {...props} probeHealthSummary={null} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
