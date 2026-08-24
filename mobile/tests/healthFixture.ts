import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import { projectProbeHealth } from "@pifire/core/dashboard/probeHealth";

export function wireHealth(
  over: Omit<Partial<ThermocoupleHealthView>, "report" | "detector" | "freshness"> & {
    report?: Partial<ThermocoupleHealthView["report"]>;
    detector?: Partial<ThermocoupleHealthView["detector"]>;
    freshness?: Partial<ThermocoupleHealthView["freshness"]>;
  } = {},
): ThermocoupleHealthView {
  const { report, detector, freshness, ...rest } = over;
  return {
    device: "mcp9601",
    port: "TC0",
    label: "Grill",
    displayName: "Grill",
    role: "Primary",
    outcome: "none",
    ...rest,
    report: {
      state: "healthy",
      faults: [],
      evidence: [],
      temperatureValid: true,
      detail: {},
      ...report,
    },
    detector: { source: "software", policy: "observe", ...detector },
    freshness: { current: true, lastReportedAgeS: 0, ...freshness },
  };
}

export const projectedHealth = (
  over?: Parameters<typeof wireHealth>[0],
) => projectProbeHealth(wireHealth(over));
