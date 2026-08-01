import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import * as api from "../../helpers/update/updateApi";
import { UpdatePage } from "./UpdatePage";

rs.mock("../../helpers/update/updateApi", () => ({
  fetchUpdateState: rs.fn(),
  fetchUpdateCheck: rs.fn(),
  fetchUpdateLog: rs.fn(),
  fetchUpdateStatus: rs.fn(),
  fetchBuildLog: rs.fn(),
  buildLogDownloadUrl: () => "/api/update/buildlog/download",
  refreshBranches: rs.fn(),
  changeBranch: rs.fn(),
  pullUpdate: rs.fn(),
  upgradeDeps: rs.fn(),
  rebuildWebUi: rs.fn(),
}));

// The real panel renders through LazyLog, which virtualizes via virtua and so
// draws zero rows in jsdom (LogViewer.test.tsx says why). What this file is
// about is whether it is offered at all, not what it draws.
rs.mock("../logs/StreamingLogPanel", () => ({
  StreamingLogPanel: () => <div data-testid="build-log-panel" />,
}));

const systemActionMock = rs.fn();
rs.mock("../../helpers/admin/adminApi", () => ({
  systemAction: (...a: unknown[]) => systemActionMock(...a),
  adminErrorText: (r: { message?: string }) => r.message ?? "failed",
}));

const state = {
  ok: true,
  status: 200,
  message: "",
  data: {
    version: "v1.8.0",
    branch: "main",
    branches: ["main", "dev"],
    remote_url: "u",
    remote_version: "v1.8.1",
    detached: null,
    web_ui_stale: false,
    web_ui_build_failed: false,
  },
};

function seed(overrides: Partial<Record<keyof typeof api, unknown>> = {}) {
  (api.fetchUpdateState as ReturnType<typeof rs.fn>).mockResolvedValue(state);
  (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { current: "v1.8.0", behind: 3 },
  });
  for (const [k, v] of Object.entries(overrides)) {
    (api[k as keyof typeof api] as ReturnType<typeof rs.fn>).mockResolvedValue(v);
  }
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <UpdatePage />
    </MemoryRouter>,
  );

// Call history accumulates across a whole file otherwise, and seed() only ever
// re-sets the resolved values -- so a `toHaveBeenCalledWith` here can be
// satisfied by some earlier test's click and assert nothing at all. Cleared,
// not reset: the implementations seed() installs have to survive.
beforeEach(() => {
  for (const fn of Object.values(api)) {
    const maybeMock = fn as { mockClear?: () => void };
    maybeMock.mockClear?.();
  }
});

afterEach(cleanup);

describe("UpdatePage", () => {
  it("shows the current version, branch and commits-behind", async () => {
    seed();
    renderPage();
    expect(await screen.findByText(/v1\.8\.0/)).toBeInTheDocument();
    expect(await screen.findByText(/3 commits behind/i)).toBeInTheDocument();
  });

  it("Change Branch posts the selected branch", async () => {
    seed({ changeBranch: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.change(screen.getByLabelText(/branch/i), { target: { value: "dev" } });
    fireEvent.click(screen.getByRole("button", { name: /change branch/i }));
    await waitFor(() => expect(api.changeBranch).toHaveBeenCalledWith("dev"));
  });

  it("Update to latest calls pullUpdate", async () => {
    seed({ pullUpdate: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    await waitFor(() => expect(api.pullUpdate).toHaveBeenCalled());
  });

  it("surfaces a 409 refusal from pullUpdate as an inline message", async () => {
    seed({ pullUpdate: { ok: false, status: 409, message: "system_active", data: null } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    expect(await screen.findByText(/stop the grill/i)).toBeInTheDocument();
  });

  it("shows an informational note instead of polling when nothing actually started", async () => {
    seed({ pullUpdate: { ok: true, status: 200, message: "", data: { started: false } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    expect(await screen.findByText(/updates run on pifire hardware/i)).toBeInTheDocument();
    expect(api.fetchUpdateStatus).not.toHaveBeenCalled();
  });

  it("surfaces a 400 branch refusal as a friendly message", async () => {
    seed({ changeBranch: { ok: false, status: 400, message: "invalid_branch", data: null } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /change branch/i }));
    expect(
      await screen.findByText(/branch is no longer available.*refresh the branch list/i),
    ).toBeInTheDocument();
  });

  it("Show log fetches and renders the git log", async () => {
    seed({
      fetchUpdateLog: { ok: true, status: 200, message: "", data: { output: "abc123 fix" } },
    });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /show log/i }));
    expect(await screen.findByText(/abc123 fix/)).toBeInTheDocument();
  });

  it("polls status after a mutation starts and reports completion", async () => {
    seed({ upgradeDeps: { ok: true, status: 200, message: "", data: { started: true } } });
    let calls = 0;
    (api.fetchUpdateStatus as ReturnType<typeof rs.fn>).mockImplementation(async () => {
      calls += 1;
      return {
        ok: true,
        status: 200,
        message: "",
        data: { percent: calls < 2 ? 40 : 101, status: "Working", output: "line" },
      };
    });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /upgrade dependencies/i }));
    expect(await screen.findByText(/complete/i)).toBeInTheDocument();
  });

  it("shows a reboot notice when the run ends at 142", async () => {
    seed({ upgradeDeps: { ok: true, status: 200, message: "", data: { started: true } } });
    (api.fetchUpdateStatus as ReturnType<typeof rs.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      message: "",
      data: { percent: 142, status: "Done", output: "" },
    });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /upgrade dependencies/i }));
    // Specific: the finish now also carries a "Reboot Now" button, so a bare
    // /reboot/i matches two elements.
    expect(await screen.findByText(/a reboot is required/i)).toBeInTheDocument();
  });
});

describe("UpdatePage web UI rebuild", () => {
  it("fires a rebuild when asked", async () => {
    /* web-react/dist is a build artifact, so a pull whose rebuild did not run
       leaves the served interface behind with no way back from the browser. */
    seed({ rebuildWebUi: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText("Actions");

    fireEvent.click(screen.getByRole("button", { name: "Rebuild web UI" }));

    await waitFor(() => expect(api.rebuildWebUi).toHaveBeenCalledTimes(1));
  });

  it("says so when the served bundle is older than the code", async () => {
    (api.fetchUpdateState as ReturnType<typeof rs.fn>).mockResolvedValue({
      ...state,
      data: { ...state.data, web_ui_stale: true },
    });
    (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      message: "",
      data: { current: "v1.8.0", behind: 0 },
    });
    renderPage();

    expect(await screen.findByRole("status")).toHaveTextContent(
      "The web interface is older than the code on disk",
    );
  });

  it("stays quiet when the bundle is current", async () => {
    seed();
    renderPage();
    await screen.findByText("Actions");

    expect(screen.queryByRole("status")).toBeNull();
  });
});

function seedFailedBuild() {
  (api.fetchUpdateState as ReturnType<typeof rs.fn>).mockResolvedValue({
    ...state,
    data: { ...state.data, web_ui_build_failed: true },
  });
  (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { current: "v1.8.0", behind: 0 },
  });
}

describe("UpdatePage failed web UI rebuild", () => {
  it("offers the build log when the last rebuild failed", async () => {
    /* The build runs detached and its output scrolls past in a status line
       polled four times a second. Without this the only copy is a shell on the
       grill. */
    seedFailedBuild();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("The last web UI rebuild failed");
    expect(screen.getByRole("button", { name: "Show build log" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download build log" })).toHaveAttribute(
      "href",
      "/api/update/buildlog/download",
    );
  });

  it("offers nothing when the rebuild worked", async () => {
    /* A build that worked has nothing to say that the interface itself does not
       already show. */
    seed();
    renderPage();
    await screen.findByText("Actions");

    expect(screen.queryByRole("button", { name: "Show build log" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Download build log" })).toBeNull();
  });

  it("mounts the panel only once the log is asked for", async () => {
    // Mounted while hidden it would poll for as long as the page was open, on
    // a grill that just failed to build.
    seedFailedBuild();
    renderPage();
    await screen.findByRole("alert");
    expect(screen.queryByTestId("build-log-panel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show build log" }));

    expect(screen.getByTestId("build-log-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide build log" }));
    expect(screen.queryByTestId("build-log-panel")).toBeNull();
  });

  it("stops polling and reports the run when a forced rebuild fails", async () => {
    /* updater.py publishes a NEGATIVE percent on failure. Only percents above
       100 used to end the poll, so a failed rebuild left the page polling a
       process that had already stopped writing, showing a bar that never
       moved -- exactly what a hung build looks like. */
    seed({
      rebuildWebUi: { ok: true, status: 200, message: "", data: { started: true } },
      fetchUpdateStatus: {
        ok: true,
        status: 200,
        message: "",
        data: {
          percent: -1,
          status: "Web UI rebuild failed",
          output: " - The build did not complete.",
        },
      },
    });
    renderPage();
    await screen.findByText("Actions");
    const stateReads = (api.fetchUpdateState as ReturnType<typeof rs.fn>).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Rebuild web UI" }));

    await waitFor(() =>
      expect(screen.getAllByText("Web UI rebuild failed").length).toBeGreaterThan(0),
    );
    // Reloading the state is what puts the build log on offer.
    await waitFor(() =>
      expect((api.fetchUpdateState as ReturnType<typeof rs.fn>).mock.calls.length).toBe(
        stateReads + 1,
      ),
    );

    const callsAfterFinish = (api.fetchUpdateStatus as ReturnType<typeof rs.fn>).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect((api.fetchUpdateStatus as ReturnType<typeof rs.fn>).mock.calls.length).toBe(
      callsAfterFinish,
    );
  });
});

function seedDetached() {
  (api.fetchUpdateState as ReturnType<typeof rs.fn>).mockResolvedValue({
    ...state,
    data: { ...state.data, branch: "", detached: "30aaae0c" },
  });
  (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { current: "v1.8.0", behind: 0 },
  });
}

describe("UpdatePage on a detached checkout", () => {
  it("says there is nothing to update to, and names the commit", async () => {
    /* An update is `git merge origin/<branch>`, so a checkout that is not on a
       branch has nothing to update to. It used to fire anyway: the branch name
       was git's `(HEAD detached at ...)` placeholder, the shell refused the
       parentheses, and the page polled "Starting Update..." for ever. */
    seedDetached();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("not on a branch");
    expect(screen.getByText("30aaae0c")).toBeInTheDocument();
  });

  it("disables only the action that needs a branch", async () => {
    seedDetached();
    renderPage();
    await screen.findByRole("alert");

    expect(screen.getByRole("button", { name: "Update to latest" })).toBeDisabled();
    // Changing branch is the way out; the other two work wherever HEAD is.
    expect(screen.getByRole("button", { name: "Change Branch" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Upgrade dependencies" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Rebuild web UI" })).toBeEnabled();
  });

  it("offers a real branch to change to, not the empty current one", async () => {
    // `branch` is "" when detached, and posting that gets a 400 from the
    // allowlist -- so the control that fixes the problem would refuse too.
    seedDetached();
    renderPage();
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: "Change Branch" }));

    await waitFor(() => expect(api.changeBranch).toHaveBeenLastCalledWith("main"));
  });

  it("shows the branch, and the commits-behind line, when on one", async () => {
    seed();
    renderPage();

    expect(await screen.findByText(/3 commits behind/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update to latest" })).toBeEnabled();
  });
});

describe("UpdatePage finishing a run", () => {
  function seedFinish(percent: number) {
    seed({
      upgradeDeps: { ok: true, status: 200, message: "", data: { started: true } },
      fetchUpdateStatus: {
        ok: true,
        status: 200,
        message: "",
        data: { percent, status: "Finished!", output: " - Finished!  Restarting Server..." },
      },
    });
    systemActionMock.mockReset();
    systemActionMock.mockResolvedValue({ ok: true, status: 200, message: "", data: null });
  }

  const finish = async () => {
    renderPage();
    await screen.findByText("Actions");
    fireEvent.click(screen.getByRole("button", { name: /upgrade dependencies/i }));
  };

  it("offers the restart the status line promises", async () => {
    /* The run publishes "Finished! Restarting Server..." and then nothing
       restarts anything -- the updater is detached and cannot restart the
       service it was launched from. The page only announced the finish, so an
       operator was left on the pre-update code with no way forward. */
    seedFinish(101);
    await finish();

    const button = await screen.findByRole("button", { name: "Restart Now" });
    fireEvent.click(button);

    await waitFor(() => expect(systemActionMock).toHaveBeenLastCalledWith("restart"));
  });

  it("offers both a reboot and a service restart when a reboot is required", async () => {
    seedFinish(142);
    await finish();

    expect(await screen.findByRole("button", { name: "Reboot Now" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restart Service Only" }));
    await waitFor(() => expect(systemActionMock).toHaveBeenLastCalledWith("restart"));

    fireEvent.click(screen.getByRole("button", { name: "Reboot Now" }));
    await waitFor(() => expect(systemActionMock).toHaveBeenLastCalledWith("reboot"));
  });

  it("surfaces a refused restart instead of appearing to do nothing", async () => {
    seedFinish(101);
    systemActionMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "system_active",
      data: null,
    });
    await finish();

    fireEvent.click(await screen.findByRole("button", { name: "Restart Now" }));

    expect(await screen.findByText("system_active")).toBeInTheDocument();
  });

  it("offers no restart when the run failed", async () => {
    seedFinish(-1);
    await finish();

    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: "Restart Now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reboot Now" })).toBeNull();
  });
});
