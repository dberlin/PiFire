import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render, screen } from "@testing-library/react";
import { InstallProgress } from "./InstallProgress";

const getStatusMock = rs.fn();
const systemActionMock = rs.fn();

rs.mock("../../helpers/wizard/wizardApi", () => ({
  getInstallStatus: (...args: unknown[]) => getStatusMock(...args),
}));

rs.mock("../../helpers/admin/adminApi", () => ({
  systemAction: (...args: unknown[]) => systemActionMock(...args),
  adminErrorText: () => "could not restart",
}));

afterEach(() => {
  cleanup();
  rs.useRealTimers();
  getStatusMock.mockReset();
  systemActionMock.mockReset();
});

describe("InstallProgress", () => {
  it("polls every 250ms, renders status/progress, and shows the reboot modal at percent 142", async () => {
    rs.useFakeTimers();
    getStatusMock
      .mockResolvedValueOnce({ percent: 10, status: "Installing…", output: "" })
      .mockResolvedValueOnce({ percent: 142, status: "Waiting for reboot", output: "" });

    const onDone = rs.fn();
    render(<InstallProgress baseUrl="http://x" onDone={onDone} />);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });
    expect(screen.getByText("Installing…")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");
    expect(getStatusMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });

    expect(screen.getByRole("dialog", { name: "Reboot required" })).toBeInTheDocument();
    // Buttons that POST, not links: /admin/reboot and /admin/restart were
    // Flask page routes and 404 now, and the API that replaced them refuses
    // GET outright.
    expect(screen.getByRole("button", { name: "Reboot Now" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart Service Only" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Reboot Now" })).toBeNull();
    expect(onDone).not.toHaveBeenCalled();
    expect(getStatusMock).toHaveBeenCalledTimes(2);

    // Polling must have stopped once the reboot modal is shown: advancing
    // well past several more intervals must not trigger further calls.
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });
    expect(getStatusMock).toHaveBeenCalledTimes(2);
  });

  it("calls onDone('restart') for any other percent above 100, and stops polling", async () => {
    rs.useFakeTimers();
    getStatusMock.mockResolvedValueOnce({ percent: 101, status: "Finishing…", output: "" });

    const onDone = rs.fn();
    render(<InstallProgress baseUrl="http://x" onDone={onDone} />);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });

    expect(onDone).toHaveBeenCalledWith("restart");
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(getStatusMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });
    expect(getStatusMock).toHaveBeenCalledTimes(1);
  });

  it("ignores an in-flight getInstallStatus response that resolves after unmount", async () => {
    rs.useFakeTimers();
    let resolveStatus: (value: { percent: number; status: string; output: string }) => void =
      () => {};
    getStatusMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStatus = resolve;
      }),
    );

    const onDone = rs.fn();
    const { unmount } = render(<InstallProgress baseUrl="http://x" onDone={onDone} />);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });
    expect(getStatusMock).toHaveBeenCalledTimes(1);

    unmount();

    // Resolve the in-flight request only after unmount: the guard must
    // discard it silently (no state update, no onDone call, no throw).
    await act(async () => {
      resolveStatus({ percent: 142, status: "Waiting for reboot", output: "" });
      await Promise.resolve();
    });

    expect(onDone).not.toHaveBeenCalled();
  });

  it("clears the interval on unmount so no further polling or state updates occur", async () => {
    rs.useFakeTimers();
    getStatusMock.mockResolvedValue({ percent: 10, status: "Installing…", output: "" });

    const onDone = rs.fn();
    const { unmount } = render(<InstallProgress baseUrl="http://x" onDone={onDone} />);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });
    expect(getStatusMock).toHaveBeenCalledTimes(1);

    unmount();
    getStatusMock.mockClear();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(2000);
    });
    expect(getStatusMock).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("clamps the progress bar width to 100% for in-range percents", async () => {
    rs.useFakeTimers();
    getStatusMock.mockResolvedValue({ percent: 55, status: "Working…", output: "" });

    render(<InstallProgress baseUrl="http://x" onDone={rs.fn()} />);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "55");
    expect(bar.firstElementChild).toHaveStyle({ width: "55%" });
  });

  it("skips the bar transition when the user prefers reduced motion", async () => {
    rs.useFakeTimers();
    getStatusMock.mockResolvedValue({ percent: 20, status: "Working…", output: "" });
    const matchMediaMock = rs.fn().mockReturnValue({ matches: true });
    // biome-ignore lint/suspicious/noExplicitAny: stubbing a browser API jsdom doesn't implement.
    (window as any).matchMedia = matchMediaMock;

    try {
      render(<InstallProgress baseUrl="http://x" onDone={rs.fn()} />);

      await act(async () => {
        await rs.advanceTimersByTimeAsync(250);
      });

      expect(matchMediaMock).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
      const bar = screen.getByRole("progressbar").firstElementChild;
      expect(bar).toHaveClass("pf-install-progress-bar-reduced-motion");
    } finally {
      // biome-ignore lint/suspicious/noExplicitAny: undo the stub above.
      (window as any).matchMedia = undefined;
    }
  });

  it("posts the system action rather than navigating to a dead /admin route", async () => {
    rs.useFakeTimers();
    getStatusMock.mockResolvedValue({ percent: 142, status: "Waiting for reboot", output: "" });
    systemActionMock.mockResolvedValue({ ok: true });

    render(<InstallProgress baseUrl="http://x" onDone={rs.fn()} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });

    await act(async () => {
      screen.getByRole("button", { name: "Restart Service Only" }).click();
    });
    expect(systemActionMock).toHaveBeenCalledWith("restart");

    await act(async () => {
      screen.getByRole("button", { name: "Reboot Now" }).click();
    });
    expect(systemActionMock).toHaveBeenCalledWith("reboot");
  });

  it("surfaces a refused system action instead of leaving the modal silent", async () => {
    rs.useFakeTimers();
    getStatusMock.mockResolvedValue({ percent: 142, status: "Waiting for reboot", output: "" });
    systemActionMock.mockResolvedValue({ ok: false, error: "not_stopped" });

    render(<InstallProgress baseUrl="http://x" onDone={rs.fn()} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(250);
    });
    await act(async () => {
      screen.getByRole("button", { name: "Reboot Now" }).click();
    });

    expect(screen.getByRole("alert")).toHaveTextContent("could not restart");
  });
});
