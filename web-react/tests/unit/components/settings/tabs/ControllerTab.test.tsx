import type { ControllerCatalog } from "@pifire/core/settings/controllerTypes";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

import { ControllerTab } from "../../../../../src/components/settings/tabs/ControllerTab";
import { renderRoute } from "../../../test-utils";

const NEURAL_WARNING_REGEX = /pre-trained neural policy|full optimisation until it is retrained/i;

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    errors: [],
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

afterEach(cleanup);
const definitionFields = {
  attributions: [],
  author: "Test",
  contributors: [],
  image: "test.png",
  link: "",
  module_name: "test",
};

const controllerMeta: ControllerCatalog = {
  metadata: {
    pid: {
      ...definitionFields,
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
    pid_sp: {
      ...definitionFields,
      friendly_name: "PID Smith Predictor",
      description: "PID with a Smith Predictor.",
      config: [
        {
          option_name: "tau",
          option_friendly_name: "Tau (s)",
          option_description: "Time constant for the Smith Predictor.",
          option_type: "float",
          option_default: 115,
          option_min: null,
          option_max: null,
        },
      ],
    },
    mpc: {
      ...definitionFields,
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
          option_max: 24,
        },
        {
          option_name: "estimator",
          option_friendly_name: "State Estimator",
          option_description: "State estimator for the fixed eight-delay grey-box model.",
          option_type: "list",
          option_default: "ekf",
          option_min: null,
          option_max: null,
          list_values: ["ekf", "kf"],
          list_labels: ["EKF (nonlinear)", "Kalman filter"],
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
        {
          option_name: "enable_identification",
          option_friendly_name: "Learn This Grill",
          option_description:
            "After each cook, refit the grey-box model from that cook and, when accepted, " +
            "use it on the next cook.",
          option_type: "bool",
          option_default: false,
          option_min: null,
          option_max: null,
        },
      ],
    },
  },
};

function makeContext(overrides: Partial<{ controllerMeta: ControllerCatalog | null }> = {}) {
  return {
    settings: { controller: { selected: "pid", config: { pid: { PB: 55 } } } },
    mode: "Stop",
    controllerMeta,
    ...overrides,
  };
}

// Field associates its <label> to the control via htmlFor/id, so
// getByLabelText(field) resolves the input directly.
function inputFor(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe("ControllerTab", () => {
  it("renders the Select with the selected controller and shows config value + fallback defaults", () => {
    renderRoute(<ControllerTab />, makeContext());

    const selector = screen.getByRole("combobox") as HTMLSelectElement;
    expect(selector).toHaveValue("pid");
    expect(Array.from(selector.options, (option) => option.value)).toEqual([
      "pid",
      "pid_sp",
      "mpc",
    ]);
    expect(screen.getByText("PID Standard")).toBeInTheDocument();
    expect(screen.getByDisplayValue("55")).toBeInTheDocument(); // PB from config
    expect(screen.getByDisplayValue("45")).toBeInTheDocument(); // Td default
    expect(screen.getByDisplayValue("180")).toBeInTheDocument(); // Ti default
    expect(screen.getByDisplayValue("0.5")).toBeInTheDocument(); // center default
  });

  it("selecting MPC renders its field and saves into the MPC config", async () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "mpc" } });

    expect(screen.getByLabelText("Prediction Horizon (steps)")).toHaveValue(24);
    expect(saveMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          controller: {
            selected: "mpc",
            config: {
              mpc: {
                n_horizon: 24,
                estimator: "ekf",
                enable_fan_input: false,
                enable_identification: false,
              },
            },
          },
        },
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

    // The pruned config, plus an EXPLICIT delete path: the backend merges, so
    // leaving the key out of `set` would leave it in the tree forever.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          __settings_delta__: 1,
          set: {
            controller: {
              selected: "pid",
              config: { pid: { PB: 55, Td: 45, Ti: 180, center: 0.5 } },
            },
          },
          delete: [["controller", "config", "pid", "ancient_option"]],
        },
        ["controller_update"],
      ),
    );
    const saved = saveMock.mock.calls[0]![0] as {
      set: { controller: { config: { pid: Record<string, unknown> } } };
    };
    expect(Object.keys(saved.set.controller.config.pid)).not.toContain("ancient_option");
    // The save SUCCEEDED and the removal is the intended cleanup, so this is a
    // status notice, not the save error it used to be reported as.
    expect(screen.getByRole("status").textContent).toContain("ancient_option");
    expect(screen.getByRole("status").textContent).toContain("Removed");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders a PID-SP-only field when the Smith Predictor controller is selected", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pid_sp" } });

    expect(inputFor("Tau (s)")).toHaveValue(115);
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

  it("renders the retained EKF/KF estimator list without retired MHE or policy fields", () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByLabelText("Controller"), { target: { value: "mpc" } });

    const estimatorSelect = screen.getByLabelText("State Estimator");
    expect(estimatorSelect.tagName).toBe("SELECT");
    expect(estimatorSelect).toHaveValue("ekf");
    expect(screen.getByText("EKF (nonlinear)")).toBeInTheDocument();
    expect(screen.getByText("Kalman filter")).toBeInTheDocument();
    expect(screen.queryByText(/MHE/)).toBeNull();
    expect(screen.queryByLabelText("Firing-Rate Policy")).toBeNull();
    expect(screen.queryByLabelText("Policy Net Path")).toBeNull();
  });

  it("saves the retained estimator and rounded native horizon", async () => {
    renderRoute(<ControllerTab />, makeContext());

    fireEvent.change(screen.getByLabelText("Controller"), { target: { value: "mpc" } });
    fireEvent.change(screen.getByLabelText("State Estimator"), { target: { value: "kf" } });
    fireEvent.change(screen.getByLabelText("Prediction Horizon (steps)"), {
      target: { value: "7.6" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          controller: {
            selected: "mpc",
            config: {
              mpc: {
                n_horizon: 8,
                estimator: "kf",
                enable_fan_input: false,
                enable_identification: false,
              },
            },
          },
        },
        ["controller_update"],
      ),
    );
  });

  it("preserves the original numeric type for list options whose list_values are numbers", async () => {
    const numericListMeta: ControllerCatalog = {
      metadata: {
        dummy: {
          ...definitionFields,
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
    const metaWithStep: ControllerCatalog = {
      metadata: {
        pid: {
          ...definitionFields,
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

// Type-level only: retained controllers receive their generated option sets.
// Runtime behaviour is unchanged and is covered by the cases above.
import type { ControllerConfigs } from "@pifire/core/settings/controllerTypes";

type MpcConfig = NonNullable<ControllerConfigs["mpc"]>;

describe("generated controller config types", () => {
  it("includes the retained PID and PID-SP option sets", () => {
    const pid: ControllerConfigs["pid"] = { PB: 60, Td: 45, Ti: 180, center: 0.5 };
    const pidSp: ControllerConfigs["pid_sp"] = {
      PB: 60,
      Td: 45,
      Ti: 180,
      stable_window: 12,
      center_factor: 0.001,
    };

    expect(pid.PB).toBe(60);
    expect(pidSp.stable_window).toBe(12);
  });

  it("constrains the retained estimator to EKF or KF", () => {
    const ekf: MpcConfig["estimator"] = "ekf";
    const kf: MpcConfig["estimator"] = "kf";
    expect([ekf, kf]).toEqual(["ekf", "kf"]);

    // @ts-expect-error -- MHE is retired from the generated settings contract.
    const mhe: MpcConfig["estimator"] = "mhe";
    expect(mhe).toBeTruthy();
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

describe("ControllerTab retired neural settings", () => {
  const staleMpcContext = () => ({
    settings: {
      platform: { dc_fan: true },
      pwm: { pwm_control: true },
      controller: {
        selected: "mpc",
        config: {
          mpc: {
            n_horizon: 12,
            estimator: "ekf",
            enable_fan_input: false,
            enable_identification: true,
            policy: "net",
            policy_net_path: "/data/legacy-policy.npz",
            t_step: 10,
            n_delay: 4,
            C_f: 19,
            mhe_horizon: 8,
          },
        },
      },
    },
    mode: "Stop",
    controllerMeta,
  });

  it("does not render a neural-policy warning for a stale saved policy", () => {
    renderRoute(<ControllerTab />, staleMpcContext());

    expect(screen.queryByText(NEURAL_WARNING_REGEX)).toBeNull();
  });

  it("explicitly deletes every undeclared retired MPC key on save", async () => {
    renderRoute(<ControllerTab />, staleMpcContext());

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    const delta = saveMock.mock.calls[0]![0] as {
      set: { controller: { config: { mpc: Record<string, unknown> } } };
      delete: string[][];
    };
    expect(delta.set.controller.config.mpc).toEqual({
      n_horizon: 12,
      estimator: "ekf",
      enable_fan_input: false,
      enable_identification: true,
    });
    expect(delta.delete).toHaveLength(6);
    expect(delta.delete).toEqual(
      expect.arrayContaining([
        ["controller", "config", "mpc", "policy"],
        ["controller", "config", "mpc", "policy_net_path"],
        ["controller", "config", "mpc", "t_step"],
        ["controller", "config", "mpc", "n_delay"],
        ["controller", "config", "mpc", "C_f"],
        ["controller", "config", "mpc", "mhe_horizon"],
      ]),
    );
  });
});
