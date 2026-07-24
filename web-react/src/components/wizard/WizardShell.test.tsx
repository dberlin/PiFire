import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import type { WizardState } from "../../helpers/wizard/wizardTypes";

const getWizardStateMock = rs.fn();
const saveDraftMock = rs.fn();
const finishWizardMock = rs.fn();
const getInstallStatusMock = rs.fn();
const scanMock = rs.fn();
const fetchModuleValuesMock = rs.fn();

rs.mock("../../helpers/wizard/wizardApi", () => ({
  getWizardState: (...args: unknown[]) => getWizardStateMock(...args),
  saveDraft: (...args: unknown[]) => saveDraftMock(...args),
  finishWizard: (...args: unknown[]) => finishWizardMock(...args),
  getInstallStatus: (...args: unknown[]) => getInstallStatusMock(...args),
  scan: (...args: unknown[]) => scanMock(...args),
  fetchModuleValues: (...args: unknown[]) => fetchModuleValuesMock(...args),
}));

const { WizardShell } = await import("./WizardShell");

afterEach(() => {
  cleanup();
  saveDraftMock.mockReset();
  finishWizardMock.mockReset();
  getInstallStatusMock.mockReset();
  scanMock.mockReset().mockResolvedValue({ groups: [], error: null });
  fetchModuleValuesMock.mockReset().mockResolvedValue({ settings: {}, config: {} });
  saveDraftMock.mockResolvedValue(true);
  getInstallStatusMock.mockResolvedValue({ percent: 0, status: "Starting install…", output: "" });
});

function fixtureState(overrides: Partial<WizardState> = {}): WizardState {
  return {
    modules_metadata: {
      grillplatform: {},
      probes: {},
      distance: {},
      display: {
        generic: { friendly_name: "Generic Display", settings_dependencies: {} },
      },
    },
    selections: { grillplatform: null, probes: null, distance: null, display: null },
    settings_dep_values: { grillplatform: {}, probes: {}, distance: {}, display: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probe_profiles: [],
    probes_units: "F",
    board_probe_maps: {},
    control_mode: "Stop",
    first_time_setup: false,
    has_draft: false,
    ...overrides,
  };
}

function renderShell(state: WizardState) {
  const router = createMemoryRouter(
    [
      {
        path: "/wizard",
        element: <WizardShell />,
        loader: () => state,
      },
    ],
    { initialEntries: ["/wizard"] },
  );
  return render(<RouterProvider router={router} />);
}

const STEP_HEADINGS = [
  "Welcome",
  "Grill Platform",
  "Probes",
  "Display",
  "Distance / Hopper",
  "Finish",
];

// Fires Next and waits for the *target* step's heading to actually appear
// before returning -- goToStep is async (it flushes a saveDraft before
// calling setStep), so a bare fireEvent.click risks racing the next
// findByRole against a still-pending state update.
async function clickNext(targetHeading: string) {
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: targetHeading });
}

async function advanceToFinish() {
  for (let i = 1; i < STEP_HEADINGS.length; i++) {
    await clickNext(STEP_HEADINGS[i]);
  }
}

describe("WizardShell", () => {
  it("renders the welcome step first", async () => {
    renderShell(fixtureState());
    expect(await screen.findByRole("heading", { name: "Welcome" })).toBeInTheDocument();
  });

  it("renders GrillPlatformStep (a module select) on the grill platform step, not the placeholder", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });

    await clickNext("Grill Platform");

    expect(screen.getByRole("combobox", { name: "Module" })).toBeInTheDocument();
    expect(screen.queryByText(/coming in a later release/i)).not.toBeInTheDocument();
  });

  it("Next advances the step and flushes a draft with the current working state", async () => {
    const state = fixtureState();
    renderShell(state);
    await screen.findByRole("heading", { name: "Welcome" });

    await clickNext("Grill Platform");

    expect(saveDraftMock).toHaveBeenCalledTimes(1);
    expect(saveDraftMock).toHaveBeenCalledWith(
      "",
      expect.objectContaining({
        selections: state.selections,
        settings_dep_values: state.settings_dep_values,
        display_config: state.display_config,
      }),
    );
  });

  it("flushes a draft reflecting an edit made just before navigating (not stale working state)", async () => {
    const state = fixtureState();
    renderShell(state);
    await screen.findByRole("heading", { name: "Welcome" });
    await clickNext("Grill Platform");
    await clickNext("Probes");
    await clickNext("Display");

    // A genuine through-the-DOM edit on the rendered DisplayStep/ModuleCard,
    // performed AFTER mount and AFTER the earlier navigations above --
    // exercises setWorking, not a direct state mutation.
    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "generic" },
    });

    saveDraftMock.mockClear();
    await clickNext("Distance / Hopper");

    expect(saveDraftMock).toHaveBeenCalledTimes(1);
    expect(saveDraftMock).toHaveBeenCalledWith(
      "",
      expect.objectContaining({
        selections: expect.objectContaining({ display: "generic" }),
      }),
    );
  });

  it("advances the step even if saveDraft rejects (navigation is best-effort on the flush)", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });
    saveDraftMock.mockRejectedValueOnce(new Error("network error"));

    await clickNext("Grill Platform");

    expect(await screen.findByRole("heading", { name: "Grill Platform" })).toBeInTheDocument();
  });

  it("Back also flushes a draft and returns to the previous step", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });
    await clickNext("Grill Platform");
    saveDraftMock.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    expect(saveDraftMock).toHaveBeenCalledTimes(1);
  });

  it("Finish on the finish step calls finishWizard with the current working state", async () => {
    const state = fixtureState();
    renderShell(state);
    await screen.findByRole("heading", { name: "Welcome" });
    await advanceToFinish();

    finishWizardMock.mockResolvedValue({ ok: true, status: 200 });
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));

    await screen.findByRole("progressbar");
    expect(finishWizardMock).toHaveBeenCalledWith(
      "",
      expect.objectContaining({ selections: state.selections }),
    );
  });

  it("{ok:true} renders InstallProgress and hides the Back/Next footer", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });
    await advanceToFinish();
    finishWizardMock.mockResolvedValue({ ok: true, status: 200 });

    fireEvent.click(screen.getByRole("button", { name: "Finish" }));

    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  });

  it("{ok:false,status:409} shows the system-active modal and does NOT render InstallProgress", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });
    await advanceToFinish();
    finishWizardMock.mockResolvedValue({ ok: false, status: 409, message: "system_active" });

    fireEvent.click(screen.getByRole("button", { name: "Finish" }));

    expect(
      await screen.findByText("Can't install while the grill is active — stop it first."),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("{ok:false,status:400} shows the missing-selection message", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });
    await advanceToFinish();
    finishWizardMock.mockResolvedValue({ ok: false, status: 400, message: "missing_selection" });

    fireEvent.click(screen.getByRole("button", { name: "Finish" }));

    expect(
      await screen.findByText(
        "Please choose a module for every hardware section before finishing.",
      ),
    ).toBeInTheDocument();
  });

  it("{ok:false,status:422} shows the bus-conflict message", async () => {
    renderShell(fixtureState());
    await screen.findByRole("heading", { name: "Welcome" });
    await advanceToFinish();
    finishWizardMock.mockResolvedValue({ ok: false, status: 422, message: "bus_conflict" });

    fireEvent.click(screen.getByRole("button", { name: "Finish" }));

    expect(
      await screen.findByText(
        "The selected I2C bus configuration conflicts — resolve it before finishing.",
      ),
    ).toBeInTheDocument();
  });

  it("disables Finish and shows a note when control_mode is not Stop", async () => {
    renderShell(fixtureState({ control_mode: "Hold" }));
    await screen.findByRole("heading", { name: "Welcome" });
    await advanceToFinish();

    const finishButton = await screen.findByRole("button", { name: "Finish" });
    expect(finishButton).toBeDisabled();
    expect(
      screen.getByText(
        "Stop the grill before finishing setup — installation can't run while it's active.",
      ),
    ).toBeInTheDocument();
    expect(finishWizardMock).not.toHaveBeenCalled();
  });
});
