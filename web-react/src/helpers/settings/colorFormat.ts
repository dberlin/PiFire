// history_page.probe_config colors are stored as the nonstandard
// "rgb(r, g, b, 1)" string form (see common/defaults.py COLOR_LIST).
// These helpers round-trip that format exactly; alpha is always 1.
export function rgbStringToHex(rgb: string): string {
  const m = rgb.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (!m) return "#000000";
  const to2 = (s: string) => Math.min(255, Number(s)).toString(16).padStart(2, "0");
  return `#${to2(m[1])}${to2(m[2])}${to2(m[3])}`;
}

export function hexToRgbString(hex: string): string {
  const m = hex.match(/^#?([0-9a-fA-F]{6})$/);
  if (!m) return "rgb(0, 0, 0, 1)";
  const v = Number.parseInt(m[1], 16);
  return `rgb(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, 1)`;
}
