import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
        {
          option_name: "estimator",
          option_type: "list",
          list_values: ["ekf", "kf"],
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
  });

  it("turns a list option into a union of its declared values, not a bare string", () => {
    // A typo'd estimator name must fail to compile; `string` would accept it.
    expect(emitControllerTypes(MANIFEST)).toContain('estimator: "ekf" | "kf";');
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
  it("emits the exact retained MPC interface from the production catalog", () => {
    const manifest = JSON.parse(
      readFileSync(resolve("../controller/controllers.json"), "utf-8"),
    ) as typeof MANIFEST;

    const out = emitControllerTypes(manifest);
    expect(out).toContain(`export interface MpcConfig {
  n_horizon: number;
  control_period: number;
  Q_w: number;
  R_dQ: number;
  C_c: number;
  h_amb: number;
  T_amb: number;
  theta: number;
  K_Q: number;
  sigma: number;
  estimator: "ekf" | "kf";
  fan_min_pct: number;
  fan_max_pct: number;
  enable_fan_input: boolean;
  est_q_temp: number;
  est_q_dist: number;
  est_r_meas: number;
  enable_identification: boolean;
  enable_online_adaptation: boolean;
}`);
    expect(out).not.toMatch(/\b(?:mhe|policy|policy_net_path|t_step|n_delay)\b/);
  });
});
