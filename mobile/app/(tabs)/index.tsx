import { ScrollView, StyleSheet, Text, View } from "react-native";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { ControlRow } from "../../src/components/ControlRow";
import { GrillGauge } from "../../src/components/GrillGauge";
import { ProbeCard } from "../../src/components/ProbeCard";
import { THEME } from "../../src/theme";
import { useLiveContext } from "../_layout";

const tokens = THEME.ember;

// deriveView's hopper/pill colors are CSS `var(--token)` strings (dashboard.css
// custom properties) -- meaningful on the web, meaningless as an RN style
// value. Same limitation GrillGauge.tsx documents for its own palette: ported
// literally from web-react/src/theme.css's base @theme block rather than
// resolved at runtime, because RN has no var() resolver. Only the tokens
// hopperView() actually emits are listed here.
const CSS_VAR_COLOR: Record<string, string> = {
  "var(--ok)": "#5ec96f", // --color-ok
  "var(--warn)": "#ffb020", // --color-warn
  "var(--danger)": "#ff5a4d", // --color-danger
  "var(--label)": "#7d7264", // --color-label
};

// The dashboard screen: the one place a user watches a live cook and, from
// the same screen, changes what the grill is doing. Every number and color
// here comes from @pifire/core/dashboard/deriveView -- the pure, shared
// presentation layer web-react's own dashboard renders from -- so this
// component's job is laying the pieces out, not deciding what they say.
export default function Dashboard() {
  const { live, phase, command } = useLiveContext();
  const view = deriveView(live);
  // A dead live socket does not mean the REST command endpoint is
  // unreachable (see ControlRow.tsx's SAFETY_LABELS note), but it DOES mean
  // the dashboard can no longer show the result of a command -- so every
  // button except Stop/Shutdown disables while not live.
  const disabled = phase !== "live";

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.gaugeWrap}>
        <GrillGauge
          temp={view.tempInt}
          stale={view.stale}
          setpoint={view.setpointInt}
          maxTemp={view.maxTemp}
          frac={view.gaugeFrac}
          hasSetpoint={view.hasSetpoint}
          modeLabel={view.modeLabel}
          units={view.units}
          cooking={view.cooking}
          animate
        />
      </View>

      {view.hasProbes ? (
        <View style={styles.probeRow}>
          {view.probes.map((p, i) => (
            <ProbeCard
              key={p.label}
              name={p.name}
              temp={p.tempInt}
              // Raw wire field, paired by index with deriveView's own
              // probes array (built from the same dash.foodProbes in the
              // same order) -- deriveView formats this into `targetStr`
              // ("→ 203°" / "AMBIENT") for the web's CSS-driven card;
              // ProbeCard does that same formatting itself from the number,
              // the way GrillGauge formats its own temp/mode text.
              target={live.foodProbes?.[i]?.target ?? 0}
              units={p.unit}
              stale={p.stale}
            />
          ))}
        </View>
      ) : null}

      <View style={styles.hopper}>
        <Text style={[styles.hopperLabel, { color: CSS_VAR_COLOR[view.hopper.labelColor] ?? tokens.text }]}>
          {view.hopper.label}
        </Text>
        <View style={styles.hopperTrack}>
          <View
            style={[
              styles.hopperFill,
              {
                width: `${view.hopper.pct}%`,
                backgroundColor: CSS_VAR_COLOR[view.hopper.color] ?? tokens.accent,
              },
            ]}
          />
        </View>
      </View>

      <ControlRow dash={live} command={command} disabled={disabled} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: tokens.background,
  },
  content: {
    alignItems: "center",
    paddingVertical: 24,
    paddingHorizontal: 16,
    gap: 20,
  },
  gaugeWrap: {
    alignItems: "center",
  },
  probeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 10,
    width: "100%",
  },
  hopper: {
    width: "100%",
    gap: 6,
  },
  hopperLabel: {
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  hopperTrack: {
    height: 10,
    borderRadius: 5,
    backgroundColor: tokens.surface,
    overflow: "hidden",
  },
  hopperFill: {
    height: "100%",
    borderRadius: 5,
  },
});
