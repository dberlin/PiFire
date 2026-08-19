import { ScrollView, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { PROBE_GAP, SCALE, probeGrid } from "@pifire/core/dashboard/scale";
import { ControlRow } from "../../src/components/ControlRow";
import { GrillGauge } from "../../src/components/GrillGauge";
import { ProbeCard } from "../../src/components/ProbeCard";
import { CAPTION, CARD_BORDER_COLOR, CSS_VAR_COLOR, LABEL_COLOR, THEME } from "../../src/theme";
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
  // The probe row always spans the content width, so it lines up with the
  // full-width hopper below it; how many columns that width is divided
  // into, and how wide each card is, comes from @pifire/core's probeGrid.
  // Card widths are computed rather than left to flex-wrap because wrap
  // sizes each card from its own text, which made a row of probes ragged.
  const { width: screenWidth } = useWindowDimensions();
  const rowWidth = screenWidth - SCREEN_PADDING * 2;
  const grid = probeGrid(view.probes.length, rowWidth);
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
              width={grid.width}
            />
          ))}
        </View>
      ) : null}

      {/* Head row (caption + percentage), level track, then the status label
          -- web-react's HopperGauge.tsx structure, with one deliberate
          departure: the track is HORIZONTAL here, where web's is a vertical
          silo.

          The vertical port was tried first and does not survive the phone
          layout. On web the silo sits in a 298pt side column, where tall and
          narrow reads as a fill level. Stretched across a phone's full
          content width it became a 361x140 slab -- larger in area than the
          gauge, so the least important number on the screen was also the
          loudest. A horizontal track carries the same fraction at a weight
          matching the probe cards, and matches .pf-dash-bar, which this app
          already renders under every probe.

          The "Manager" shortcut into /pellets (dashboard.css's
          .pf-dash-hopper-link) is left out -- this app has no pellet-manager
          route to link to yet. */}
      <View style={[styles.hopper, { backgroundColor: tokens.surface, borderColor: CARD_BORDER_COLOR }]}>
        <View style={styles.hopperHead}>
          <Text style={styles.hopperCaption}>Hopper</Text>
          <Text style={[styles.hopperVal, { color: CSS_VAR_COLOR[view.hopper.color] ?? tokens.accent }]}>
            {view.hopper.pct}%
          </Text>
        </View>
        <View style={styles.hopperTrack} testID="hopper-track">
          <View
            testID="hopper-fill"
            style={[
              styles.hopperFill,
              {
                width: `${view.hopper.pct}%`,
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

// The dashboard's horizontal inset. Shared with the probe-grid maths above,
// which must subtract exactly what the ScrollView's content padding adds.
const SCREEN_PADDING = 16;

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    alignItems: "center",
    paddingVertical: 24,
    paddingHorizontal: SCREEN_PADDING,
    gap: 20,
  },
  gaugeWrap: {
    alignItems: "center",
  },
  // Cards carry explicit widths from probeGrid, so wrapping here only ever
  // breaks a row at the column count that grid chose -- it never re-sizes a
  // card. `flex-start` rather than `center`: with uniform widths a full row is
  // already flush, and a trailing partial row should align with the column
  // above it rather than drift to the middle.
  probeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "flex-start",
    gap: PROBE_GAP,
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
    ...CAPTION,
    color: LABEL_COLOR,
  },
  hopperVal: {
    fontSize: SCALE.phone.hopperVal,
    fontWeight: "800",
  },
  // Same metrics as ProbeCard's .pf-dash-bar track, so the hopper reads as a
  // sibling of the probe cards rather than as a slab. See the departure note
  // at the call site for why this is horizontal where web's is vertical.
  hopperTrack: {
    height: 10,
    borderRadius: 6,
    overflow: "hidden",
    backgroundColor: "rgba(255, 255, 255, 0.09)",
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.12)",
  },
  hopperFill: {
    height: "100%",
    borderRadius: 6,
  },
  hopperLabel: CAPTION,
});
