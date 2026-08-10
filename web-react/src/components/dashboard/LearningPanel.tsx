import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { Units } from "../../helpers/settings/settingsTypes.gen";
import { MpcLearningView } from "./learning/MpcLearningView";
import { PidSpLearningView } from "./learning/PidSpLearningView";

interface LearningPanelProps {
  apiBase: string;
  selectedController: string | null;
  units: Units;
  ambientC: number;
  modelLearningRevision?: string | null;
}

export function LearningPanel({
  apiBase,
  selectedController,
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
        selectedController={selectedController}
        modelLearningRevision={modelLearningRevision}
      />
    );
  }

  return null;
}
