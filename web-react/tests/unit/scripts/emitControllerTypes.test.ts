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
    pid_parallel: {
      config: [
        { option_name: "Kp", option_type: "float" },
        { option_name: "Clamping", option_type: "bool" },
      ],
    },
    fuzzy: { config: [] },
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
    expect(out).toContain("Kp: number;");
    expect(out).toContain("Clamping: boolean;");
    expect(out).toContain("n_horizon: number;");
    expect(out).toContain("policy_net_path: string;");
  });

  it("turns a list option into a union of its declared values, not a bare string", () => {
    // A typo'd estimator name must fail to compile; `string` would accept it.
    expect(emitControllerTypes(MANIFEST)).toContain('estimator: "ekf" | "mhe" | "kf";');
  });

  it("gives a controller that declares no options an empty record, not an index signature", () => {
    // Record<string, never> keeps `fuzzy` closed. An index signature would put
    // back exactly the looseness this emitter exists to remove.
    expect(emitControllerTypes(MANIFEST)).toContain(
      "export type FuzzyConfig = Record<string, never>;",
    );
  });

  it("maps every controller into one keyed interface", () => {
    const out = emitControllerTypes(MANIFEST);
    expect(out).toContain("export interface ControllerConfigs {");
    expect(out).toContain("  pid: PidConfig;");
    expect(out).toContain("  pid_parallel: PidParallelConfig;");
    expect(out).toContain("  fuzzy: FuzzyConfig;");
    expect(out).toContain("  mpc: MpcConfig;");
  });

  it("carries the do-not-edit banner", () => {
    expect(emitControllerTypes(MANIFEST)).toContain("do not edit");
  });
});
