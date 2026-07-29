// Shared copy for the "N commits behind" ladder, consumed by both
// UpdatePage and SystemUpdateCard so the wording can't drift between them.

export function behindText(behind: number | null): string {
  if (behind === null) return "Update status unavailable";
  if (behind > 0) return `${behind} commits behind`;
  return "Up to date";
}
