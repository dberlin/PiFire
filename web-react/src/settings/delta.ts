export function setPath(obj: object, path: string, value: unknown): object {
  const keys = path.split(".");
  const root: Record<string, unknown> = { ...(obj as Record<string, unknown>) };
  let cur = root as Record<string, unknown>;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    cur[k] = { ...(cur[k] as Record<string, unknown> ?? {}) };
    cur = cur[k] as Record<string, unknown>;
  }
  cur[keys[keys.length - 1]] = value;
  return root;
}
