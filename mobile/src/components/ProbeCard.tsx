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
  /** Raw target temperature (the wire's `foodProbes[i].target`), 0 or below
   *  meaning "no target armed". Formatting it into "→ 203°"/"AMBIENT" is pure
   *  presentation, the same kind of decision GrillGauge makes turning a
   *  number into on-screen text -- not a business decision like which modes
   *  a button offers. */
  target: number;
  units: "F" | "C";
  /** Set when `temp` is a carried-over reading, e.g. "last data 47s ago". */
  stale: string | null;
}

// No accent selector yet, matching GrillGauge.tsx's note: theme.ts's gauge
// gradient/glow tokens (and, by the same limitation, any per-card accent) are
// deferred to a later task. Card chrome uses the default (ember) tokens.
const tokens = THEME.ember;

export function ProbeCard({ name, temp, target, units, stale }: ProbeCardProps) {
  const hasTarget = target > 0;

  return (
    <View style={styles.card}>
      <Text style={styles.name}>{name}</Text>
      <View style={styles.readingRow}>
        <Text style={styles.tempInt}>{temp === null ? "—" : temp}</Text>
        <Text style={styles.tempUnit}>{"°"}{units}</Text>
      </View>
      {stale !== null ? <Text style={styles.stale}>{stale}</Text> : null}
      <Text style={hasTarget ? styles.target : styles.targetAmbient}>
        {hasTarget ? `→ ${target}°` : "AMBIENT"}
      </Text>
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
