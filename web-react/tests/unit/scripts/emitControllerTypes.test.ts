import { describe, expect, it } from "@rstest/core";
import { emitControllerTypes } from "../../../scripts/emitControllerTypes";

const MANIFEST = {
  metadata: {
    pid: {
      config: [
        { option_name: "PB", option_type: "float" },
        { option_name: "Td", option_type: "float" },
      ],
    },
    pid_sp: {
      config: [{ option_name: "tau", option_type: "float" }],
    },
    no_options: { config: [] },
    mpc: {
      config: [
        { option_name: "n_horizon", option_type: "int" },
        { option_name: "policy_net_path", option_type: "string" },
        {
          option_name: "estimator",
          option_type: "list",
          list_values: ["ekf", "mhe", "kf"],
        },
      ],
    },
  },
};

describe("emitControllerTypes", () => {
  it("maps each declared option type to its TypeScript counterpart", () => {
    const out = emitControllerTypes(MANIFEST);
    expect(out).toContain("export interface PidConfig {\n  PB: number;\n  Td: number;\n}");
    expect(out).toContain("export interface PidSpConfig {\n  tau: number;\n}");
    expect(out).toContain("n_horizon: number;");
    expect(out).toContain("policy_net_path: string;");
  });

  it("turns a list option into a union of its declared values, not a bare string", () => {
    // A typo'd estimator name must fail to compile; `string` would accept it.
    expect(emitControllerTypes(MANIFEST)).toContain('estimator: "ekf" | "mhe" | "kf";');
  });

  it("gives a controller that declares no options an empty record, not an index signature", () => {
    // Record<string, never> keeps an optionless controller closed. An index
    // signature would put back exactly the looseness this emitter exists to remove.
    expect(emitControllerTypes(MANIFEST)).toContain(
      "export type NoOptionsConfig = Record<string, never>;",
    );
  });

  it("maps every controller into one keyed interface", () => {
    const out = emitControllerTypes(MANIFEST);
    expect(out).toContain("export interface ControllerConfigs {");
    expect(out).toContain("  pid: PidConfig;");
    expect(out).toContain("  pid_sp: PidSpConfig;");
    expect(out).toContain("  no_options: NoOptionsConfig;");
    expect(out).toContain("  mpc: MpcConfig;");
  });

  it("carries the do-not-edit banner", () => {
    expect(emitControllerTypes(MANIFEST)).toContain("do not edit");
  });
});
