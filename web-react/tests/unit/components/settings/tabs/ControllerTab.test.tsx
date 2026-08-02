import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { ControllerTab } from "../../../../../src/components/settings/tabs/ControllerTab";
import type { ControllerMetadata } from "../../../../../src/helpers/settings/settingsApi";
import { renderRoute } from "../../../test-utils";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

afterEach(cleanup);

const controllerMeta: ControllerMetadata = {
  metadata: {
    pid: {
      friendly_name: "PID Standard",
      description: "The standard PID controller for PiFire.",
      config: [
        {
          option_name: "PB",
          option_friendly_name: "Proportional Band(PB)",
          option_description: "Proportional band.",
          option_type: "float",
          option_default: 60.0,
          option_min: null,
          option_max: null,
        },
        {
          option_name: "Td",
          option_friendly_name: "Derivative Time (Td)",
          option_description: "Derivative time.",
          option_type: "float",
          option_default: 45.0,
          option_min: null,
          option_max: null,
        },
        {
          option_name: "Ti",
          option_friendly_name: "Integral Time (Ti)",
          option_description: "Integral time.",
          option_type: "float",
          option_default: 180.0,
          option_min: null,
          option_max: null,
        },
        {
          option_name: "center",
          option_friendly_name: "Center Ratio",
          option_description: "Center of cycle ratio.",
          option_type: "float",
          option_default: 0.5,
          option_min: null,
          option_max: null,
        },
      ],
    },
    fuzzy: {
      friendly_name: "Fuzzy Logic Controller",
      description: "Experimental fuzzy logic controller.",
      config: [],
    },
    pid_parallel: {
      friendly_name: "Parallel PID w/ optional Integrator Clamping",
      description: "PID in parallel form with optional integral anti-windup protection.",
      config: [
        {
          option_name: "Clamping",
          option_friendly_name: "Integral Windup Protection",
          option_description: "Stops integration when output limits are exceeded.",
          option_type: "bool",
          option_default: true,
          option_min: null,
          option_max: null,
        },
      ],
    },
    mpc: {
      friendly_name: "Model Predictive Control (MPC)",
      description: "Experimental MPC controller.",
      config: [
        {
          option_name: "n_horizon",
          option_friendly_name: "Prediction Horizon (steps)",
          option_description: "Number of prediction steps.",
          option_type: "int",
          option_default: 24,
          option_min: 5,
          option_max: 60,
        },
        {
          option_name: "estimator",
          option_friendly_name: "State Estimator",
          option_description: "Disturbance/state estimator.",
          option_type: "list",
          option_default: "ekf",
          option_min: null,
          option_max: null,
          list_values: ["ekf", "mhe", "kf"],
          list_labels: ["EKF (nonlinear, fast)", "MHE (nonlinear)", "Kalman (linear)"],
        },
        {
          option_name: "policy_net_path",
          option_friendly_name: "Policy Net Path",
          option_description: "Path to the trained neural-net policy artifact.",
          option_type: "string",
          option_default: "./controller/mpc_policy_net.npz",
          option_min: null,
          option_max: null,
        },
        {
          option_name: "enable_fan_input",
          option_friendly_name: "MPC Controls Fan",
          option_description: "If enabled, the MPC commands fan duty (PWM/DC-fan builds only).",
          option_type: "bool",
          option_default: false,
          option_min: null,
          option_max: null,
        },
      ],
    },
  },
};

function makeContext(overrides: Partial<{ controllerMeta: ControllerMetadata | null }> = {}) {
  return {
    settings: { controller: { selected: "pid", config: { pid: { PB: 55 } } } },
    mode: "Stop",
    controllerMeta,
    ...overrides,
  };
}

// NumberField wraps its input in a <label> whose text also carries the suffix,
// so getByLabelText(field) does not match. Reach the input via the label span.
function inputFor(label: string): HTMLInputElement {
  const input = screen.getByText(label).closest("label")?.querySelector("input");
  if (!input) throw new Error(`no input for field "${label}"`);
  return input;
}

describe("ControllerTab", () => {
  it("renders the Select with the selected controller and shows config value + fallback defaults", () => {
    renderRoute(<ControllerTab />, makeContext());

    expect(screen.getByRole("combobox")).toHaveValue("pid");
    expect(screen.getByText("PID Standard")).toBeInTheDocument();
    expect(screen.getByDisplayValue("55")).toBeInTheDocument(); // PB from config
    expect(screen.getByDisplayValue("45")).toBeInTheDocument(); // Td default
    expect(screen.getByDisplayValue("180")).toBeInTheDocument(); // Ti default
    expect(screen.getByDisplayValue("0.5")).toBeInTheDocument(); // center default
  });

  it("switching the Select to fuzzy hides fields and shows the no-config hint without saving", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fuzzy" } });

    expect(screen.queryByDisplayValue("55")).not.toBeInTheDocument();
    expect(screen.getByText(/no configuration options/i)).toBeInTheDocument();
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("saves an empty config for a controller with no declared options", async () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fuzzy" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Wait for the save call carrying the empty config.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        { controller: { selected: "fuzzy", config: { fuzzy: {} } } },
        ["controller_update"],
      ),
    );
  });

  it("drops an option the selected controller does not declare and reports it, instead of saving it", async () => {
    renderRoute(<ControllerTab />, {
      settings: {
        controller: { selected: "pid", config: { pid: { PB: 55, ancient_option: "leftover" } } },
      },
      mode: "Stop",
      controllerMeta,
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Wait for the save call carrying the pruned config.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          controller: {
            selected: "pid",
            config: { pid: { PB: 55, Td: 45, Ti: 180, center: 0.5 } },
          },
        },
        ["controller_update"],
      ),
    );
    const saved = saveMock.mock.calls[0]![0] as {
      controller: { config: { pid: Record<string, unknown> } };
    };
    expect(Object.keys(saved.controller.config.pid)).not.toContain("ancient_option");
    expect(screen.getByRole("alert").textContent).toContain("ancient_option");
  });

  it("renders a Toggle for a bool-typed option when a controller with one is selected", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pid_parallel" } });

    expect(screen.getByRole("button", { name: "Integral Windup Protection" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("saves the exact delta after editing PB, rebuilding the whole pid config with coerced floats", async () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByDisplayValue("55"), { target: { value: "62.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Wait for the save call carrying the rebuilt pid config.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          controller: {
            selected: "pid",
            config: {
              pid: { PB: 62.5, Td: 45, Ti: 180, center: 0.5 },
            },
          },
        },
        ["controller_update"],
      ),
    );
  });

  it("renders a Select for list options and a TextField for string options with default values", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByLabelText("Controller"), { target: { value: "mpc" } });

    const estimatorSelect = screen.getByLabelText("State Estimator");
    expect(estimatorSelect.tagName).toBe("SELECT");
    expect(estimatorSelect).toHaveValue("ekf");
    expect(screen.getByText("EKF (nonlinear, fast)")).toBeInTheDocument();
    expect(screen.getByText("Kalman (linear)")).toBeInTheDocument();

    const policyPathField = screen.getByLabelText("Policy Net Path");
    expect(policyPathField.tagName).toBe("INPUT");
    expect(policyPathField).toHaveValue("./controller/mpc_policy_net.npz");
  });

  it("saves list (mapped back to the original metadata value), string, and Math.round-coerced int values for mpc", async () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByLabelText("Controller"), { target: { value: "mpc" } });
    fireEvent.change(screen.getByLabelText("State Estimator"), { target: { value: "kf" } });
    fireEvent.change(screen.getByLabelText("Policy Net Path"), {
      target: { value: "./custom/net.npz" },
    });
    fireEvent.change(screen.getByLabelText("Prediction Horizon (steps)"), {
      target: { value: "7.6" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Wait for the save call carrying the coerced mpc config.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          controller: {
            selected: "mpc",
            config: {
              mpc: {
                n_horizon: 8,
                estimator: "kf",
                policy_net_path: "./custom/net.npz",
                enable_fan_input: false,
              },
            },
          },
        },
        ["controller_update"],
      ),
    );
  });

  it("preserves the original numeric type for list options whose list_values are numbers", async () => {
    const numericListMeta: ControllerMetadata = {
      metadata: {
        dummy: {
          friendly_name: "Dummy",
          description: "",
          config: [
            {
              option_name: "level",
              option_friendly_name: "Level",
              option_description: "",
              option_type: "list",
              option_default: 1,
              option_min: null,
              option_max: null,
              list_values: [1, 2, 3],
              list_labels: ["Low", "Medium", "High"],
            },
          ],
        },
      },
    };

    renderRoute(<ControllerTab />, {
      settings: { controller: { selected: "dummy", config: { dummy: {} } } },
      mode: "Stop",
      controllerMeta: numericListMeta,
    });

    fireEvent.change(screen.getByLabelText("Level"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Wait for the save call carrying the type-preserved level.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        { controller: { selected: "dummy", config: { dummy: { level: 3 } } } },
        ["controller_update"],
      ),
    );
    // Assert the numeric type is preserved, not just its loose-equal string form.
    const delta = saveMock.mock.calls[0]![0] as {
      controller: { config: { dummy: { level: unknown } } };
    };
    expect(delta.controller.config.dummy.level).toBe(3);
    expect(typeof delta.controller.config.dummy.level).toBe("number");
  });

  it("shows an error state and no Select when controllerMeta is null", () => {
    renderRoute(<ControllerTab />, makeContext({ controllerMeta: null }));

    expect(screen.getByText(/controller metadata unavailable/i)).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
  // _macro_settings.html:51 forwards option['option_step']; ControllerTab
  // passed option_min/option_max but dropped the step, so a float option whose
  // real granularity is 1e-10 got the browser's default step of 1 and its
  // spinner became useless.
  it("forwards option_step to the NumberField", () => {
    const metaWithStep: ControllerMetadata = {
      metadata: {
        pid: {
          friendly_name: "PID Standard",
          description: "",
          config: [
            {
              option_name: "PB",
              option_friendly_name: "Proportional Band(PB)",
              option_description: "",
              option_type: "float",
              option_default: 60.0,
              option_min: 0,
              option_max: 100,
              option_step: 0.001,
            },
            {
              option_name: "Ti",
              option_friendly_name: "Integral Time (Ti)",
              option_description: "",
              option_type: "float",
              option_default: 180.0,
              option_min: null,
              option_max: null,
              option_step: null,
            },
          ],
        },
      },
    };

    renderRoute(<ControllerTab />, {
      settings: { controller: { selected: "pid", config: {} } },
      mode: "Stop",
      controllerMeta: metaWithStep,
    });

    expect(inputFor("Proportional Band(PB)")).toHaveAttribute("step", "0.001");
    // A null option_step must not emit step="null" — it stays absent.
    expect(inputFor("Integral Time (Ti)")).not.toHaveAttribute("step");
  });
});

// Type-level only: the generated map must name every controller and give each
// its own option set, so indexing one with another's option is a compile error.
// Runtime behaviour is unchanged and is covered by the cases above.
import type { ControllerConfigs } from "../../../../../src/helpers/settings/controllerTypes.gen";

describe("generated controller config types", () => {
  it("gives each controller its own option set", () => {
    const pid: ControllerConfigs["pid"] = { PB: 60, Td: 45, Ti: 180, center: 0.5 };
    expect(pid.PB).toBe(60);

    // @ts-expect-error -- Kp belongs to pid_parallel, not pid
    const wrong: ControllerConfigs["pid"] = { PB: 60, Td: 45, Ti: 180, center: 0.5, Kp: 1 };
    expect(wrong).toBeTruthy();
  });

  it("constrains a list option to its declared values", () => {
    const ok: ControllerConfigs["mpc"]["estimator"] = "ekf";
    expect(ok).toBe("ekf");

    // @ts-expect-error -- "ekfx" is not one of ekf | mhe | kf
    const typo: ControllerConfigs["mpc"]["estimator"] = "ekfx";
    expect(typo).toBeTruthy();
  });
});

const mpcContext = (pwmControl: boolean, dcFan = true) => ({
  settings: {
    platform: { dc_fan: dcFan },
    pwm: { pwm_control: pwmControl },
    controller: { selected: "mpc", config: { mpc: { enable_fan_input: true } } },
  },
  mode: "Stop",
  controllerMeta,
});

describe("ControllerTab MPC fan authority", () => {
  it("shows a blocking error when MPC owns the fan but PWM control is off", () => {
    renderRoute(<ControllerTab />, mpcContext(false));
    expect(screen.getByRole("alert")).toHaveTextContent(/PWM Control is off/i);
  });

  it("refuses to save while the conflict stands", () => {
    renderRoute(<ControllerTab />, mpcContext(false));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("has no error and saves normally when PWM control is on", () => {
    renderRoute(<ControllerTab />, mpcContext(true));
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saveMock).toHaveBeenCalled();
  });

  it("does not fire on an AC-fan build", () => {
    renderRoute(<ControllerTab />, mpcContext(false, false));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("clears once MPC Controls Fan is toggled off", () => {
    renderRoute(<ControllerTab />, mpcContext(false));
    fireEvent.click(screen.getByRole("button", { name: "MPC Controls Fan" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
