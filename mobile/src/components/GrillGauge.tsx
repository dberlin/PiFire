import { useEffect } from "react";
import { StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Defs, Line, LinearGradient, Path, RadialGradient, Stop } from "react-native-svg";
import Animated, {
  Easing,
  cancelAnimation,
  useAnimatedProps,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from "react-native-reanimated";
import { arcLength, describeArc, polarToCartesian, valueAngle } from "@pifire/core/gaugeMath";
import { SCALE, gaugeModeBadge } from "@pifire/core/dashboard/scale";
import type { ProbeHealthView } from "@pifire/core/dashboard/probeHealth";
import { ProbeHealthInline } from "./HealthBanner";
import {
  GAUGE_ACCENT,
  SETPOINT_COLOR,
  TEXT_COLOR,
  TEXT_DIM_COLOR,
  LABEL_COLOR,
  CAPTION,
  TRACK_COLOR,
  WARN_COLOR,
  THEME,
  withAlpha,
  type AccentName,
} from "../theme";

// react-native-svg's Path/Circle need wrapping to accept Reanimated's
// `animatedProps` (the SVG attribute equivalent of `useAnimatedStyle`).
const AnimatedPath = Animated.createAnimatedComponent(Path);
const AnimatedCircle = Animated.createAnimatedComponent(Circle);

// dashboard.css's .pf-dash-gauge-glow is `width/height: var(--pf-gauge-ring)`
// against a `--pf-gauge-size` SVG -- the glow disc is smaller than the SVG
// box, not the same size as it, so it reads as a soft ring bleeding past the
// arc rather than a disc filling the whole card. --pf-gauge-ring / --pf-gauge-size
// is 360/392 at desktop and 236/260 at the phone breakpoint -- both ~0.91 --
// applied to this component's fixed 220 viewBox.
// The SVG keeps its 220-unit viewBox -- CX/CY/R below are viewBox
// coordinates, so the drawing is unchanged -- and is *rendered* at the
// shared gaugeSize. Rendering it at 220 was drift from the shared scale,
// and it was load-bearing: the mode badge is 17px/3-tracking (identical to
// web's .pf-dash-gauge-mode), which needs a ~151pt chord, and a 220 box
// only offers ~142pt where the badge sits -- so MONITOR overhung the arc.
const VIEWBOX = 220;
const GAUGE_SIZE = SCALE.phone.gaugeSize;
const GLOW_RADIUS = (VIEWBOX / 2) * (SCALE.phone.gaugeRing / SCALE.phone.gaugeSize);
// Scaled from the desktop reference rather than copied from it: the badge
// sits inside the arc, so it has to keep its share of the ring at this
// gauge's size. See gaugeModeBadge's note for why this one element scales
// uniformly where every other size is a per-element token.
const MODE_BADGE = gaugeModeBadge(GAUGE_SIZE);

interface GrillGaugeProps {
  /** Selects the gradient stops, glow, and mode-badge color -- the only parts
   *  of this component that vary by accent (see theme.ts's GAUGE_ACCENT and
   *  its own doc comment for why the rest of the gauge's palette doesn't). */
  accent: AccentName;
  /** Already rounded by deriveView, and already the last real reading when the
   *  probe has no current one. Null only when it has produced nothing at all. */
  temp: number | null;
  /** Set when `temp` is a carried-over reading, e.g. "last data 47s ago". */
  stale: string | null;
  setpoint: number;
  maxTemp: number;
  frac: number;
  hasSetpoint: boolean;
  modeLabel: string;
  units: "F" | "C";
  cooking: boolean;
  animate: boolean;
  /** Shared, framework-free health presentation for the Primary probe. */
  health?: ProbeHealthView | null;
}

// Same drawing coordinates as web-react/src/components/dashboard/GrillGauge.tsx
// (CX/CY/R, the -135..135 track, the arcLength-and-offset value arc). The
// track and its length never change, so they are module-level constants
// rather than re-derived every render.
const CX = 110;
const CY = 110;
const R = 90;
const TRACK = describeArc(CX, CY, R, -135, 135);
const LEN = arcLength(R);

// dashboard.css's @keyframes pf-glow: opacity 0.3 <-> 0.62 over 3.2s
// ease-in-out infinite, only while the CSS `animation` shorthand is set
// (i.e. only when `cooking && animate`).
const GLOW_MIN_OPACITY = 0.3;
const GLOW_MAX_OPACITY = 0.62;
const GLOW_HALF_CYCLE_MS = 1600; // 3.2s full cycle, up then down

// Center piece: 270° ember arc (geometry from @pifire/core/gaugeMath, shared
// with the web component so the two can never disagree about where a
// temperature sits on the arc), a setpoint tick, an animated glow, and the
// big grill temperature + mode badge overlay.
export function GrillGauge({
  accent,
  temp,
  stale,
  setpoint,
  maxTemp,
  frac,
  hasSetpoint,
  modeLabel,
  units,
  cooking,
  animate,
  health = null,
}: GrillGaugeProps) {
  const accentColor = THEME[accent].accent;
  const gaugeAccent = GAUGE_ACCENT[accent];
  const dashOffset = useSharedValue(LEN * (1 - frac));
  const glowOpacity = useSharedValue(GLOW_MIN_OPACITY);

  // The value arc eases toward the new reading over 250ms with an OutCubic
  // curve -- matching web-react's --anim-ms (250ms) / --ease-out-cubic
  // (cubic-bezier(0.33, 1, 0.68, 1), i.e. Easing.out(Easing.cubic)) tokens.
  useEffect(() => {
    dashOffset.value = withTiming(LEN * (1 - frac), {
      duration: 250,
      easing: Easing.out(Easing.cubic),
    });
  }, [frac, dashOffset]);

  // The glow pulse runs ONLY while actively cooking AND animation is
  // enabled -- an unconditional loop would drain a phone battery over a
  // 12-hour cook. When either condition drops, the loop is cancelled and
  // the glow settles back to its resting opacity instead of freezing
  // mid-pulse.
  useEffect(() => {
    if (cooking && animate) {
      glowOpacity.value = withRepeat(
        withSequence(
          withTiming(GLOW_MAX_OPACITY, { duration: GLOW_HALF_CYCLE_MS, easing: Easing.inOut(Easing.ease) }),
          withTiming(GLOW_MIN_OPACITY, { duration: GLOW_HALF_CYCLE_MS, easing: Easing.inOut(Easing.ease) }),
        ),
        -1,
        false,
      );
    } else {
      cancelAnimation(glowOpacity);
      glowOpacity.value = withTiming(GLOW_MIN_OPACITY, { duration: 200 });
    }
    return () => cancelAnimation(glowOpacity);
  }, [cooking, animate, glowOpacity]);

  const arcAnimatedProps = useAnimatedProps(() => ({
    strokeDashoffset: dashOffset.value,
  }));
  // Drives the glow Circle's `opacity` prop directly (not a wrapping View's
  // style) -- see the Circle below.
  const glowAnimatedProps = useAnimatedProps(() => ({
    opacity: glowOpacity.value,
  }));

  const spAngle = valueAngle(setpoint, maxTemp);
  const inner = polarToCartesian(CX, CY, R - 13, spAngle);
  const outer = polarToCartesian(CX, CY, R + 9, spAngle);

  return (
    <View style={styles.container} testID="gauge">
      <View style={styles.card}>
      <Svg width={GAUGE_SIZE} height={GAUGE_SIZE} viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}>
        <Defs>
          <LinearGradient id="pfGauge" x1="0" y1="1" x2="1" y2="0">
            <Stop offset="0" stopColor={gaugeAccent.arcStop0} />
            <Stop offset="0.55" stopColor={gaugeAccent.arcStop1} />
            <Stop offset="1" stopColor={gaugeAccent.arcStop2} />
          </LinearGradient>
          {/* dashboard.css's .pf-dash-gauge-glow: radial-gradient(closest-side,
              var(--accent), transparent 68%) -- opaque at the center, straight
              to fully transparent by 68% of the radius, held transparent past
              that. CSS's `filter: blur(6px)` on top of it has no RN
              equivalent; the gradient's own linear fade from opaque to
              transparent is the approximation for the blur, not an extra
              effect layered on it. */}
          <RadialGradient id="pfGaugeGlow" cx="0.5" cy="0.5" r="0.5">
            <Stop offset="0" stopColor={gaugeAccent.glow} stopOpacity="1" />
            <Stop offset="0.68" stopColor={gaugeAccent.glow} stopOpacity="0" />
          </RadialGradient>
        </Defs>
        {/* Behind the track/arc, same as .pf-dash-gauge-glow sitting behind
            .pf-dash-gauge-svg in the DOM. A filled circle with fill="none"'s
            opposite -- radial-gradient fill, not a flat color -- is what keeps
            this a soft ring rather than the solid disc the earlier flat View
            painted. */}
        <AnimatedCircle
          cx={CX}
          cy={CY}
          r={GLOW_RADIUS}
          fill="url(#pfGaugeGlow)"
          animatedProps={glowAnimatedProps}
          testID="gauge-glow"
        />
        <Path d={TRACK} fill="none" stroke={TRACK_COLOR} strokeWidth={16} strokeLinecap="round" />
        <AnimatedPath
          d={TRACK}
          fill="none"
          stroke="url(#pfGauge)"
          strokeWidth={16}
          strokeLinecap="round"
          strokeDasharray={LEN}
          animatedProps={arcAnimatedProps}
        />
        {hasSetpoint && (
          <Line
            x1={inner.x}
            y1={inner.y}
            x2={outer.x}
            y2={outer.y}
            stroke={SETPOINT_COLOR}
            strokeWidth={4}
            strokeLinecap="round"
          />
        )}
      </Svg>
      <View style={styles.overlay} pointerEvents="none">
        <Text style={styles.caption}>Grill</Text>
        <View style={styles.num}>
          <Text style={styles.temp}>{temp === null ? "—" : temp}</Text>
          {temp === null ? null : <Text style={styles.unit}>{`°${units}`}</Text>}
        </View>
        {stale && <Text style={styles.stale}>{stale}</Text>}
        {hasSetpoint && <Text style={styles.set}>{`SET ${Math.round(setpoint)}°`}</Text>}
        {/* dashboard.css's .pf-dash-gauge-mode: a small pill BADGE (14%-alpha
            fill, 55%-alpha border, uppercase, letter-spaced), not a large
            primary block -- it names the current mode, it isn't the headline. */}
        <Text
          style={[
            styles.mode,
            MODE_BADGE,
            {
              backgroundColor: withAlpha(accentColor, 0.14),
              borderColor: withAlpha(accentColor, 0.55),
              color: accentColor,
            },
          ]}
        >
          {modeLabel}
        </Text>
      </View>
      </View>
      <ProbeHealthInline health={health} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: GAUGE_SIZE,
    alignItems: "center",
  },
  card: {
    width: GAUGE_SIZE,
    height: GAUGE_SIZE,
    alignItems: "center",
    justifyContent: "center",
  },
  overlay: {
    position: "absolute",
    alignItems: "center",
    gap: 2,
  },
  caption: {
    ...CAPTION,
    color: LABEL_COLOR,
  },
  num: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  temp: {
    fontSize: SCALE.phone.gaugeNum,
    fontWeight: "800",
    color: TEXT_COLOR,
  },
  unit: {
    fontSize: SCALE.phone.gaugeUnit,
    fontWeight: "600",
    color: TEXT_DIM_COLOR,
    marginLeft: 4,
  },
  stale: {
    fontSize: 14,
    fontWeight: "600",
    letterSpacing: 0.4,
    color: WARN_COLOR,
    marginTop: 2,
  },
  set: {
    fontSize: 20,
    fontWeight: "600",
    letterSpacing: 1,
    color: SETPOINT_COLOR,
    marginTop: 2,
  },
  // dashboard.css's .pf-dash-gauge-mode literals, unscaled. The rule is
  // declared once there with no media override, so web draws the badge at
  // this size at every breakpoint -- matching it is what keeps the phone app
  // and web's phone tier identical.
  // Size, padding, tracking and the gap above all come from MODE_BADGE --
  // they scale with the gauge. Only what does not scale lives here.
  mode: {
    borderRadius: 999,
    textTransform: "uppercase",
    fontWeight: "700",
  },
});
