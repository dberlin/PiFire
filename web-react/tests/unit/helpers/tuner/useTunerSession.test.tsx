import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, render, screen, waitFor } from "@testing-library/react";

import * as actualTunerApi from "../../../../src/helpers/tuner/tunerApi" with {
  rstest: "importActual",
};

const openMock = rs.fn();
const closeMock = rs.fn();
rs.mock("../../../../src/helpers/tuner/tunerApi", () => ({
  ...actualTunerApi,
  openSession: (...a: unknown[]) => openMock(...a),
  closeSession: (...a: unknown[]) => closeMock(...a),
}));

const { useTunerSession } = await import("../../../../src/helpers/tuner/useTunerSession");

const OK = (data: unknown) => ({ ok: true, status: 200, message: "", data });

function Host() {
  const session = useTunerSession("");
  return (
    <div>
      <span data-testid="status">{session.status}</span>
      <span data-testid="error">{session.error ?? ""}</span>
      <button type="button" onClick={session.start}>
        start
      </button>
      <button type="button" onClick={session.stop}>
        stop
      </button>
    </div>
  );
}

beforeEach(() => {
  openMock.mockReset();
  closeMock.mockReset();
  closeMock.mockResolvedValue(OK({ open: false, mode: "Stop", restored: true }));
});

describe("useTunerSession", () => {
  it("starts idle and opens nothing on mount", () => {
    render(<Host />);
    expect(screen.getByTestId("status")).toHaveTextContent("idle");
    //  Mounting must NOT move the grill. Navigating to /tuner and reading the
    //  instructions is not consent to switch the grill into Monitor.
    expect(openMock).not.toHaveBeenCalled();
  });

  it("opens on start", async () => {
    openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("open"));
  });

  it("reports a refusal without claiming the session opened", async () => {
    openMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_tunable",
      data: null,
      mode: "Hold",
    });
    render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("refused"));
    expect(screen.getByTestId("error")).toHaveTextContent("Hold");
  });

  it("CLOSES THE SESSION ON UNMOUNT", async () => {
    // The single most important assertion here. A page that unmounts without
    // closing leaves the operator's grill in Monitor with tuning_mode set, and
    // nothing on screen to say so.
    openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    const view = render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("open"));

    view.unmount();
    await waitFor(() => expect(closeMock).toHaveBeenCalledTimes(1));
  });

  it("does not close on unmount when it never opened", () => {
    render(<Host />).unmount();
    expect(closeMock).not.toHaveBeenCalled();
  });

  it("closes on unmount even when the open is still in flight", async () => {
    //  Navigating away mid-open is the race that leaves a session orphaned:
    //  the open lands on a page that no longer exists, so nothing ever closes
    //  it. The hook must close once the open resolves.
    let resolveOpen!: (v: unknown) => void;
    openMock.mockReturnValue(
      new Promise((r) => {
        resolveOpen = r;
      }),
    );
    const view = render(<Host />);
    act(() => screen.getByText("start").click());
    view.unmount();

    await act(async () => {
      resolveOpen(OK({ open: true, mode: "Monitor", restored: true }));
    });
    await waitFor(() => expect(closeMock).toHaveBeenCalledTimes(1));
  });

  it("stop closes once and returns to idle", async () => {
    openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("open"));

    act(() => screen.getByText("stop").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("idle"));
    expect(closeMock).toHaveBeenCalledTimes(1);
  });
});
