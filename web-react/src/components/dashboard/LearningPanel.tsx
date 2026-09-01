import type { Units } from "@pifire/core/settings/settingsTypes";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { MpcLearningView } from "./learning/MpcLearningView";
import { PidSpLearningView } from "./learning/PidSpLearningView";

interface LearningPanelProps {
  apiBase: string;
  selectedController: string | null;
  currentMode: string;
  displayMode: string;
  criticalError: boolean;
  units: Units;
  ambientC: number;
  modelLearningRevision?: string | null;
}

export function LearningPanel({
  apiBase,
  selectedController,
  currentMode,
  displayMode,
  criticalError,
  units,
  ambientC,
  modelLearningRevision,
}: LearningPanelProps) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (selectedController === "mpc") {
      const queryKey = ["model-evidence-report", apiBase] as const;
      return () => queryClient.removeQueries({ queryKey, exact: true });
    }
    if (selectedController === "pid_sp") {
      const queryKey = ["learning-report", "pid_sp", apiBase] as const;
      return () => queryClient.removeQueries({ queryKey, exact: true });
    }
    return undefined;
  }, [apiBase, queryClient, selectedController]);
  if (selectedController === "mpc") {
    return (
      <MpcLearningView
        apiBase={apiBase}
        selectedController={selectedController}
        currentMode={currentMode}
        displayMode={displayMode}
        criticalError={criticalError}
        units={units}
        ambientC={ambientC}
        modelLearningRevision={modelLearningRevision}
      />
    );
  }

  if (selectedController === "pid_sp") {
    return (
      <PidSpLearningView
        apiBase={apiBase}
        currentMode={currentMode}
        displayMode={displayMode}
        criticalError={criticalError}
        selectedController={selectedController}
        modelLearningRevision={modelLearningRevision}
      />
    );
  }

  return null;
}
