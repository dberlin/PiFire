export const LEARNING_SECTION_CLASS = "min-w-0 rounded-card border border-card-border bg-inset p-4";

const STATUS_TONE: Record<string, string> = {
  "insufficient excitation": "text-warn",
  fitting: "text-accent",
  evaluating: "text-accent",
  "ready for review": "text-ok",
  activating: "text-accent",
  active: "text-ok",
  fallback: "text-warn",
  error: "text-danger",
  "schema invalidated": "text-danger",
};

export function normalizeLearningStatus(status: string): string {
  const normalized = status.trim().replace(/[-_]+/g, " ").replace(/\s+/g, " ").toLowerCase();
  return normalized || "unavailable";
}

export function learningPillStatus(
  status: string,
  currentMode: string,
  displayMode: string,
  criticalError: boolean,
): string {
  const normalized = normalizeLearningStatus(status);
  if (
    criticalError ||
    currentMode === "Error" ||
    displayMode === "Error" ||
    normalized === "error"
  ) {
    return "error";
  }
  if (normalized === "collecting" && displayMode !== "Hold") return "idle";
  return normalized;
}

export function learningStatusLabel(status: string): string {
  const normalized = normalizeLearningStatus(status);
  return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}`;
}

export function learningStatusTone(status: string): string {
  return STATUS_TONE[normalizeLearningStatus(status)] ?? "text-probe-label";
}
