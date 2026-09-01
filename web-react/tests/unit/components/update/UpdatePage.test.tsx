import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { UpdatePage } from "../../../../src/components/update/UpdatePage";
import * as api from "../../../../src/helpers/update/updateApi";

rs.mock("../../../../src/helpers/update/updateApi", () => ({
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
  rebuildAcados: rs.fn(),
}));

const apiMocks = {
  fetchUpdateState: api.fetchUpdateState as Mock,
  fetchUpdateCheck: api.fetchUpdateCheck as Mock,
  fetchUpdateLog: api.fetchUpdateLog as Mock,
  fetchUpdateStatus: api.fetchUpdateStatus as Mock,
  fetchBuildLog: api.fetchBuildLog as Mock,
  refreshBranches: api.refreshBranches as Mock,
  changeBranch: api.changeBranch as Mock,
  pullUpdate: api.pullUpdate as Mock,
  upgradeDeps: api.upgradeDeps as Mock,
  rebuildWebUi: api.rebuildWebUi as Mock,
  rebuildAcados: api.rebuildAcados as Mock,
};

// The real panel renders through LazyLog, which virtualizes via virtua and so
// draws zero rows in jsdom (LogViewer.test.tsx says why). What this file is
// about is whether it is offered at all, not what it draws.
rs.mock("../../../../src/components/logs/StreamingLogPanel", () => ({
  StreamingLogPanel: () => <div data-testid="build-log-panel" />,
}));

const systemActionMock = rs.fn();
rs.mock("../../../../src/helpers/admin/adminApi", () => ({
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
    restart_pending: false,
    manual_dependency_actions: [],
  },
};

//  What GET /api/update/status answers when no run is going. The store keeps
//  the last run's numbers forever, and percent 0 is what a mutation route
//  writes just before launching the updater, so this is also what a launch
//  that never got off the ground leaves behind.
const IDLE_STATUS = {
  ok: true,
  status: 200,
  message: "",
  data: { percent: 0, status: "", output: "" },
};

function seed(overrides: Partial<Record<keyof typeof apiMocks, unknown>> = {}) {
  apiMocks.fetchUpdateState.mockResolvedValue(state);
  //  The page reads this on MOUNT now, to reattach to a run already in
  //  progress, so every test needs it resolvable whether or not it clicks
  //  anything.
  apiMocks.fetchUpdateStatus.mockResolvedValue(IDLE_STATUS);
  apiMocks.fetchUpdateCheck.mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { current: "v1.8.0", behind: 3 },
  });
  for (const [key, value] of Object.entries(overrides)) {
    apiMocks[key as keyof typeof apiMocks].mockResolvedValue(value);
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
  for (const mock of Object.values(apiMocks)) {
    mock.mockClear();
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
    //  Not "never called": mount reads the status once to see whether a run is
    //  already going. What must not happen is the 250ms POLL, which would sit
    //  there forever against a run that was never launched.
    const afterMount = apiMocks.fetchUpdateStatus.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(apiMocks.fetchUpdateStatus.mock.calls.length).toBe(afterMount);
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
      //  Call 1 is the mount's "is a run already going?" read and has to look
      //  idle -- an in-flight percent there makes the page attach on its own,
      //  and the click below would then prove nothing.
      const percent = calls === 1 ? 0 : calls < 3 ? 40 : 101;
      return {
        ok: true,
        status: 200,
        message: "",
        data: { percent, status: "Working", output: "line" },
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

describe("UpdatePage Acados rebuild", () => {
  it("places the action below web UI rebuild and fires it when asked", async () => {
    seed({ rebuildAcados: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText("Actions");

    const webUiButton = screen.getByRole("button", { name: "Rebuild web UI" });
    const acadosButton = screen.getByRole("button", { name: "Rebuild Acados" });
    expect(webUiButton.nextElementSibling).toBe(acadosButton);

    fireEvent.click(acadosButton);

    await waitFor(() => expect(api.rebuildAcados).toHaveBeenCalledTimes(1));
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
    expect(screen.getByRole("button", { name: "Rebuild Acados" })).toBeEnabled();
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
  function seedFinish(percent: number, stateOverrides: Record<string, unknown> = {}) {
    seed({
      upgradeDeps: { ok: true, status: 200, message: "", data: { started: true } },
      fetchUpdateStatus: {
        ok: true,
        status: 200,
        message: "",
        data: { percent, status: "Finished!", output: " - Finished!" },
      },
    });
    if (Object.keys(stateOverrides).length > 0) {
      apiMocks.fetchUpdateState.mockResolvedValue({
        ...state,
        data: { ...state.data, ...stateOverrides },
      });
    }
    systemActionMock.mockReset();
    systemActionMock.mockResolvedValue({ ok: true, status: 200, message: "", data: null });
  }

  const finish = async () => {
    renderPage();
    await screen.findByText("Actions");
    fireEvent.click(screen.getByRole("button", { name: /upgrade dependencies/i }));
  };

  it("says the updater is restarting PiFire, and offers no button to do it", async () => {
    /* The updater restarts supervisor's programs itself when the grill is
       stopped, so there is nothing left to ask for. The old "Restart Now"
       button was the ONLY thing that ever loaded an update's code, and it
       existed only while this component happened to be mounted -- which is how
       a finished update ended up serving pre-update Python for days. */
    seedFinish(101);
    await finish();

    expect(await screen.findByText(/PiFire is restarting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart Now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart Anyway" })).toBeNull();
  });

  it("asks before restarting when the run finished with the grill running", async () => {
    /* `supervisorctl restart all` stops the control process, so an automatic
       restart mid-cook drops the fire. The updater publishes restart_pending
       instead and the page asks. */
    seedFinish(101, { restart_pending: true });
    await finish();

    expect(await screen.findByText("Restart required — the grill is running")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restart Anyway" }));

    await waitFor(() => expect(systemActionMock).toHaveBeenLastCalledWith("restart"));
  });

  it("restarts nothing when the answer is Restart Later", async () => {
    seedFinish(101, { restart_pending: true });
    await finish();

    fireEvent.click(await screen.findByRole("button", { name: "Restart Later" }));

    expect(screen.queryByText("Restart required — the grill is running")).toBeNull();
    expect(systemActionMock).not.toHaveBeenCalled();
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
    seedFinish(101, { restart_pending: true });
    systemActionMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "system_active",
      data: null,
    });
    await finish();

    fireEvent.click(await screen.findByRole("button", { name: "Restart Anyway" }));

    expect(await screen.findByText("system_active")).toBeInTheDocument();
  });

  it("offers no restart when the run failed", async () => {
    seedFinish(-1);
    await finish();

    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: "Restart Anyway" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reboot Now" })).toBeNull();
  });
});

describe("UpdatePage reattaching to a run it did not start", () => {
  /* Progress used to live only in this component's memory, set by the click
     that started the run. A reload during a multi-minute update -- or reaching
     for a phone instead of the laptop -- left it null, which switched the poll
     off, which meant the finished state never arrived and the restart it asks
     for was never offered. The update completed and nothing ever said so. */

  it("polls a run already in progress, with nothing clicked", async () => {
    seed({
      fetchUpdateStatus: {
        ok: true,
        status: 200,
        message: "",
        data: { percent: 40, status: "Installing Python Dependencies...", output: "line" },
      },
    });
    renderPage();

    expect(await screen.findByText("Installing Python Dependencies...")).toBeInTheDocument();
    const seen = apiMocks.fetchUpdateStatus.mock.calls.length;
    await waitFor(() => expect(apiMocks.fetchUpdateStatus.mock.calls.length).toBeGreaterThan(seen));
  });

  it("reaches the finished state of a run started somewhere else", async () => {
    let calls = 0;
    seed();
    apiMocks.fetchUpdateStatus.mockImplementation(async () => {
      calls += 1;
      return {
        ok: true,
        status: 200,
        message: "",
        data: { percent: calls < 2 ? 60 : 101, status: "Finished!", output: "" },
      };
    });
    renderPage();

    expect(await screen.findByText(/PiFire is restarting/i)).toBeInTheDocument();
  });

  it("does NOT resurrect the last run's finished state on a later visit", async () => {
    /* The store keeps a terminal percent forever, so honouring 101 on mount
       would put "Update complete" on the page at every visit from now on.
       What legitimately outlives a run is restart_pending, which has an owner
       that clears it -- app.py, at its own boot. */
    seed({
      fetchUpdateStatus: {
        ok: true,
        status: 200,
        message: "",
        data: { percent: 101, status: "Finished!", output: " - Finished!" },
      },
    });
    renderPage();
    await screen.findByText("Actions");

    expect(screen.queryByText(/update complete/i)).toBeNull();
    expect(screen.queryByText(/PiFire is restarting/i)).toBeNull();
  });

  it("ignores a percent 0 left behind by a launch that never happened", async () => {
    seed();
    renderPage();
    await screen.findByText("Actions");

    const afterMount = apiMocks.fetchUpdateStatus.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(apiMocks.fetchUpdateStatus.mock.calls.length).toBe(afterMount);
  });
});

describe("UpdatePage pending restart", () => {
  beforeEach(() => {
    systemActionMock.mockReset();
    systemActionMock.mockResolvedValue({ ok: true, status: 200, message: "", data: null });
  });

  const seedPending = () => {
    seed();
    apiMocks.fetchUpdateState.mockResolvedValue({
      ...state,
      data: { ...state.data, restart_pending: true },
    });
  };

  it("asks on a plain visit, with no run in this tab at all", async () => {
    /* The whole point of the flag being server state: the tab that ran the
       update is usually long gone by the time anyone reads the answer. */
    seedPending();
    renderPage();

    expect(await screen.findByText("Restart required — the grill is running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart Anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart Later" })).toBeInTheDocument();
  });

  it("says why it did not restart on its own", async () => {
    seedPending();
    renderPage();

    const message = await screen.findByText(/drop an active fire/i);
    expect(message.textContent).toMatch(/running the code from before/i);
  });

  it("shows persistent manual dependency actions and withholds restart controls", async () => {
    seed();
    apiMocks.fetchUpdateState.mockResolvedValue({
      ...state,
      data: {
        ...state.data,
        restart_pending: true,
        manual_dependency_actions: [
          "Install OS package: libusb",
          "Run command: board-config.py --spi",
        ],
      },
    });

    renderPage();

    expect(await screen.findByText("Manual dependency action required")).toBeInTheDocument();
    expect(screen.getByText("Install OS package: libusb")).toBeInTheDocument();
    expect(screen.getByText("Run command: board-config.py --spi")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart Anyway" })).toBeNull();
  });

  it("stays quiet when nothing is pending", async () => {
    seed();
    renderPage();
    await screen.findByText("Actions");

    expect(screen.queryByText("Restart required — the grill is running")).toBeNull();
  });
});
