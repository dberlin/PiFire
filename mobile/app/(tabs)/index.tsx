import { ScrollView, StyleSheet, Text, View } from "react-native";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { ControlRow } from "../../src/components/ControlRow";
import { GrillGauge } from "../../src/components/GrillGauge";
import { ProbeCard } from "../../src/components/ProbeCard";
import { CARD_BORDER_COLOR, CSS_VAR_COLOR, LABEL_COLOR, THEME } from "../../src/theme";
import { useLiveContext, usePrefsContext } from "../_layout";

// The dashboard screen: the one place a user watches a live cook and, from
// the same screen, changes what the grill is doing. Every number and color
// here comes from @pifire/core/dashboard/deriveView -- the pure, shared
// presentation layer web-react's own dashboard renders from -- so this
// component's job is laying the pieces out, not deciding what they say.
export default function Dashboard() {
  const { live, phase, command } = useLiveContext();
  const { prefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];
  const view = deriveView(live);
  // A dead live socket does not mean the REST command endpoint is
  // unreachable (see ControlRow.tsx's SAFETY_LABELS note), but it DOES mean
  // the dashboard can no longer show the result of a command -- so every
  // button except Stop/Shutdown disables while not live.
  const disabled = phase !== "live";

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: tokens.background }]}
      contentContainerStyle={styles.content}
    >
      <View style={styles.gaugeWrap}>
        <GrillGauge
          accent={prefs.accent}
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
          {view.probes.map((p) => (
            <ProbeCard
              key={p.label}
              name={p.name}
              temp={p.tempInt}
              // Straight from deriveView's ProbeCardView -- already gated on
              // `fp.target > 0 && fp.targetReq`, not just `target > 0`. No
              // raw `live.foodProbes` read (and no index-pairing between two
              // separately-ordered lists) here: `p` already carries
              // everything this card needs.
              targetStr={p.targetStr}
              units={p.unit}
              stale={p.stale}
              tgtColor={CSS_VAR_COLOR[p.tgtColor] ?? tokens.accent}
              barPct={p.barPct}
              barColor={CSS_VAR_COLOR[p.barColor] ?? tokens.accent}
            />
          ))}
        </View>
      ) : null}

      {/* Same two-row layout as web-react's HopperGauge.tsx: a head row
          (caption + big percentage) over a vertical level track, then a foot
          row for the status label. The "Manager" shortcut into /pellets
          (dashboard.css's .pf-dash-hopper-link) is left out -- this app has
          no pellet-manager route to link to yet. */}
      <View style={[styles.hopper, { backgroundColor: tokens.surface, borderColor: CARD_BORDER_COLOR }]}>
        <View style={styles.hopperHead}>
          <Text style={styles.hopperCaption}>Hopper</Text>
          <Text style={[styles.hopperVal, { color: CSS_VAR_COLOR[view.hopper.color] ?? tokens.accent }]}>
            {view.hopper.pct}%
          </Text>
        </View>
        <View style={styles.hopperTrack}>
          <View
            style={[
              styles.hopperFill,
              {
                height: `${view.hopper.pct}%`,
                backgroundColor: CSS_VAR_COLOR[view.hopper.color] ?? tokens.accent,
              },
            ]}
          />
        </View>
        <Text style={[styles.hopperLabel, { color: CSS_VAR_COLOR[view.hopper.labelColor] ?? tokens.text }]}>
          {view.hopper.label}
        </Text>
      </View>

      <ControlRow dash={live} command={command} disabled={disabled} accent={prefs.accent} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
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
  // dashboard.css's .pf-dash-hopper: rounded 18, padded 16, a vertical
  // gap-12 stack of the head row / track / foot label -- ported directly,
  // unlike the horizontal thin bar this replaced.
  hopper: {
    width: "100%",
    borderRadius: 18,
    borderWidth: 1,
    padding: 16,
    gap: 12,
  },
  hopperHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  hopperCaption: {
    fontSize: 13,
    fontWeight: "600",
    letterSpacing: 2.5,
    textTransform: "uppercase",
    color: LABEL_COLOR,
  },
  // dashboard.css's phone breakpoint (max-width: 719px) --pf-hopper-val: 26px
  // -- the closest web analog to this card's actual on-device size.
  hopperVal: {
    fontSize: 26,
    fontWeight: "800",
  },
  // .pf-dash-hopper-track: a fixed-height vertical silo, not a thin
  // horizontal bar -- .pf-dash-hopper-fill fills it from the BOTTOM up
  // (position: absolute; bottom: 0; height: var(--pf-hopper-pct)).
  hopperTrack: {
    height: 140,
    borderRadius: 14,
    overflow: "hidden",
    position: "relative",
    backgroundColor: "rgba(255, 255, 255, 0.09)",
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.12)",
  },
  hopperFill: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
  },
  hopperLabel: {
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 2,
    textTransform: "uppercase",
  },
});
