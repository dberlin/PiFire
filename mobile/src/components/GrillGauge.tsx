import { useEffect } from "react";
import { StyleSheet, Text, View } from "react-native";
import Svg, { Defs, Line, LinearGradient, Path, Stop } from "react-native-svg";
import Animated, {
  Easing,
  cancelAnimation,
  useAnimatedProps,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from "react-native-reanimated";
import { arcLength, describeArc, polarToCartesian, valueAngle } from "@pifire/core/gaugeMath";
import {
  GAUGE_ACCENT,
  SETPOINT_COLOR,
  TEXT_COLOR,
  TEXT_DIM_COLOR,
  LABEL_COLOR,
  TRACK_COLOR,
  WARN_COLOR,
  THEME,
  type AccentName,
} from "../theme";

// react-native-svg's Path needs to be wrapped to accept Reanimated's
// `animatedProps` (the SVG attribute equivalent of `useAnimatedStyle`).
const AnimatedPath = Animated.createAnimatedComponent(Path);

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
  const glowStyle = useAnimatedStyle(() => ({ opacity: glowOpacity.value }));

  const spAngle = valueAngle(setpoint, maxTemp);
  const inner = polarToCartesian(CX, CY, R - 13, spAngle);
  const outer = polarToCartesian(CX, CY, R + 9, spAngle);

  return (
    <View style={styles.card} testID="gauge">
      <Animated.View
        style={[styles.glow, glowStyle, { backgroundColor: gaugeAccent.glow }]}
        testID="gauge-glow"
      />
      <Svg width={220} height={220} viewBox="0 0 220 220">
        <Defs>
          <LinearGradient id="pfGauge" x1="0" y1="1" x2="1" y2="0">
            <Stop offset="0" stopColor={gaugeAccent.arcStop0} />
            <Stop offset="0.55" stopColor={gaugeAccent.arcStop1} />
            <Stop offset="1" stopColor={gaugeAccent.arcStop2} />
          </LinearGradient>
        </Defs>
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
          <Text style={styles.unit}>{`°${units}`}</Text>
        </View>
        {stale && <Text style={styles.stale}>{stale}</Text>}
        {hasSetpoint && <Text style={styles.set}>{`SET ${Math.round(setpoint)}°`}</Text>}
        <Text style={[styles.mode, { borderColor: accentColor, color: accentColor }]}>{modeLabel}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    width: 220,
    height: 220,
    alignItems: "center",
    justifyContent: "center",
  },
  glow: {
    position: "absolute",
    width: 220,
    height: 220,
    borderRadius: 110,
  },
  overlay: {
    position: "absolute",
    alignItems: "center",
    gap: 2,
  },
  caption: {
    fontSize: 14,
    fontWeight: "600",
    letterSpacing: 4,
    textTransform: "uppercase",
    color: LABEL_COLOR,
  },
  num: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  temp: {
    fontSize: 56,
    fontWeight: "800",
    color: TEXT_COLOR,
  },
  unit: {
    fontSize: 20,
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
  mode: {
    marginTop: 12,
    paddingVertical: 6,
    paddingHorizontal: 20,
    borderRadius: 999,
    borderWidth: 1.5,
    textTransform: "uppercase",
    fontSize: 17,
    fontWeight: "700",
    letterSpacing: 3,
  },
});
