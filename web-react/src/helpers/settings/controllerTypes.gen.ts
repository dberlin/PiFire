/* eslint-disable */
// GENERATED from controller/controllers.json — do not edit. Regenerate: bun run gen:types

export interface PidConfig {
  PB: number;
  Td: number;
  Ti: number;
  center: number;
}

export interface PidSpConfig {
  PB: number;
  Td: number;
  Ti: number;
  stable_window: number;
  center_factor: number;
}

export interface MpcConfig {
  n_horizon: number;
  t_step: number;
  control_period: number;
  Q_w: number;
  R_dQ: number;
  C_c: number;
  h_amb: number;
  T_amb: number;
  theta: number;
  n_delay: number;
  K_Q: number;
  sigma: number;
  estimator: "ekf" | "mhe" | "kf";
  policy: "nlp" | "net";
  policy_net_path: string;
  fan_min_pct: number;
  fan_max_pct: number;
  enable_fan_input: boolean;
  est_q_temp: number;
  est_q_dist: number;
  est_r_meas: number;
  enable_identification: boolean;
}

export interface ControllerConfigs {
  pid: PidConfig;
  pid_sp: PidSpConfig;
  mpc: MpcConfig;
}
