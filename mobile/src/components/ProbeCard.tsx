import { StyleSheet, Text, View } from "react-native";
import { THEME } from "../theme";

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
}

// No accent selector yet, matching GrillGauge.tsx's note: theme.ts's gauge
// gradient/glow tokens (and, by the same limitation, any per-card accent) are
// deferred to a later task. Card chrome uses the default (ember) tokens.
const tokens = THEME.ember;

export function ProbeCard({ name, temp, targetStr, units, stale }: ProbeCardProps) {
  const hasTarget = targetStr !== "AMBIENT";

  return (
    <View style={styles.card}>
      <Text style={styles.name}>{name}</Text>
      <View style={styles.readingRow}>
        <Text style={styles.tempInt}>{temp === null ? "—" : temp}</Text>
        <Text style={styles.tempUnit}>{"°"}{units}</Text>
      </View>
      {stale !== null ? <Text style={styles.stale}>{stale}</Text> : null}
      <Text style={hasTarget ? styles.target : styles.targetAmbient}>{targetStr}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: tokens.surface,
    borderRadius: 12,
    padding: 12,
    gap: 4,
    minWidth: 120,
  },
  name: {
    color: tokens.text,
    fontSize: 13,
    fontWeight: "600",
  },
  readingRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 2,
  },
  tempInt: {
    color: tokens.text,
    fontSize: 28,
    fontWeight: "700",
  },
  tempUnit: {
    color: tokens.text,
    fontSize: 14,
    opacity: 0.7,
    marginBottom: 4,
  },
  stale: {
    color: tokens.danger,
    fontSize: 11,
  },
  target: {
    color: tokens.accent,
    fontSize: 13,
  },
  targetAmbient: {
    color: tokens.text,
    fontSize: 13,
    opacity: 0.5,
  },
});
