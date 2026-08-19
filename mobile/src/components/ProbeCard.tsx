import { StyleSheet, Text, View } from "react-native";
import { CARD_BORDER_COLOR, LABEL_COLOR, PROBE_LABEL_COLOR, TEXT_COLOR, TEXT_DIM_COLOR, THEME, WARN_COLOR } from "../theme";

interface ProbeCardProps {
  /** The probe's display title (deriveView's ProbeCardView.name, i.e.
   *  fp.title -- free text the user can rename, distinct from its `label`). */
  name: string;
  /** Already the last real reading when there is no current one (deriveView's
   *  ProbeCardView.tempInt); null only when the probe has produced nothing at
   *  all. Not recomputed here -- the carry-over/staleness decision stays in
   *  @pifire/core/dashboard/deriveView, same as the gauge's `temp`/`stale`. */
  temp: number | null;
  /** Already formatted by deriveView's `probeCard()` -- "→ 203°" when the
   *  probe has an ARMED target (`fp.target > 0 && fp.targetReq`), else
   *  "AMBIENT". Deliberately NOT recomputed from a raw wire field here: an
   *  earlier version of this component took a raw `target: number` and
   *  gated display on `target > 0` alone, which rendered a target for a
   *  probe with a STORED BUT DISARMED one (`targetReq: false`) -- exactly
   *  the phone/web divergence the shared derivation exists to prevent. */
  targetStr: string;
  units: "F" | "C";
  /** Set when `temp` is a carried-over reading, e.g. "last data 47s ago". */
  stale: string | null;
  /** deriveView's ProbeCardView.tgtColor, already resolved from its
   *  var(--ok)/var(--cooking)/var(--label) form to a hex/rgba string by the
   *  caller (theme.ts's CSS_VAR_COLOR has no per-accent entry, so "AMBIENT"
   *  falls back to LABEL_COLOR here when the caller omits it). */
  tgtColor?: string;
  /** deriveView's ProbeCardView.barPct -- 0 for a probe with no armed target,
   *  else progress toward it, floored at 2 so a just-started probe still
   *  shows a sliver (deriveView.ts's probeCard()). */
  barPct?: number;
  /** deriveView's ProbeCardView.barColor, resolved the same way as tgtColor. */
  barColor?: string;
}

// No accent selector yet, matching GrillGauge.tsx's note: theme.ts's gauge
// gradient/glow tokens (and, by the same limitation, any per-card accent) are
// deferred to a later task. Card chrome uses the default (ember) tokens.
const tokens = THEME.ember;

export function ProbeCard({
  name,
  temp,
  targetStr,
  units,
  stale,
  tgtColor,
  barPct = 0,
  barColor,
}: ProbeCardProps) {
  const hasTarget = targetStr !== "AMBIENT";

  return (
    <View style={styles.card}>
      <Text style={styles.name}>{name}</Text>
      <View style={styles.readingRow}>
        <Text style={styles.tempInt}>{temp === null ? "—" : temp}</Text>
        <Text style={styles.tempUnit}>{"°"}{units}</Text>
      </View>
      {stale !== null ? <Text style={styles.stale}>{stale}</Text> : null}
      <Text style={[styles.target, { color: tgtColor ?? (hasTarget ? tokens.accent : LABEL_COLOR) }]}>
        {targetStr}
      </Text>
      {/* dashboard.css's .pf-dash-bar / .pf-dash-bar-fill: a thin progress
          track under every probe card, not just ones with an armed target --
          barPct is 0 (an empty track) for "AMBIENT" rather than the whole
          element disappearing. */}
      <View style={styles.bar}>
        <View style={[styles.barFill, { width: `${barPct}%`, backgroundColor: barColor ?? tokens.accent }]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: tokens.surface,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: CARD_BORDER_COLOR,
    paddingVertical: 15,
    paddingHorizontal: 18,
    gap: 2,
    minWidth: 140,
  },
  name: {
    color: PROBE_LABEL_COLOR,
    fontSize: 15,
    fontWeight: "600",
    letterSpacing: 1.5,
    textTransform: "uppercase",
  },
  readingRow: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  // Sized to dashboard.css's phone breakpoint (max-width: 719px), the closest
  // web ever gets to this component's actual footprint: --pf-probe-temp: 44px,
  // --pf-probe-unit: 18px.
  tempInt: {
    color: TEXT_COLOR,
    fontSize: 44,
    fontWeight: "800",
  },
  tempUnit: {
    color: TEXT_DIM_COLOR,
    fontSize: 18,
    fontWeight: "600",
    marginLeft: 2,
  },
  stale: {
    color: WARN_COLOR,
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 0.4,
    marginTop: 2,
  },
  target: {
    fontSize: 15,
    fontWeight: "600",
  },
  bar: {
    height: 6,
    borderRadius: 6,
    marginTop: 8,
    overflow: "hidden",
    backgroundColor: "rgba(255, 255, 255, 0.11)",
  },
  barFill: {
    height: "100%",
    borderRadius: 6,
  },
});
