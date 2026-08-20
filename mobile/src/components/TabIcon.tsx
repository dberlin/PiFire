import type { ColorValue } from "react-native";
import Svg, { Circle, Path } from "react-native-svg";

// The four tab-bar glyphs. Drawn inline for the same reason NavBar.tsx's
// stopwatch is on the web -- neither client carries an icon font, and this
// app has no icon package at all, so a tab without its own tabBarIcon gets
// React Navigation's missing-icon placeholder rather than nothing.
//
// Every glyph is stroke-only on a 24-unit box so it takes the tab bar's
// active/inactive tint straight through `color`, exactly as the labels do.
export type TabIconName = "dashboard" | "history" | "events" | "preferences";

/** The testID each glyph carries, so an end-to-end flow can assert the real
 *  icon is on screen rather than expo-router's MissingIcon placeholder. */
export function tabIconTestID(name: TabIconName): string {
  return `tab-icon-${name}`;
}

const VIEW_BOX = 24;
const STROKE_WIDTH = 1.8;

export function TabIcon({
  name,
  color,
  size = 24,
}: {
  name: TabIconName;
  /** The tint React Navigation resolved for this tab's current state. */
  color: ColorValue;
  size?: number;
}) {
  // Stroke width is given in viewBox units, so it thins out as `size` shrinks
  // unless it is scaled back up by the same factor the box is scaled down.
  const stroke = STROKE_WIDTH * (VIEW_BOX / size);
  const common = {
    stroke: color,
    strokeWidth: stroke,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    fill: "none",
  };

  return (
    <Svg
      testID={tabIconTestID(name)}
      width={size}
      height={size}
      viewBox={`0 0 ${VIEW_BOX} ${VIEW_BOX}`}
    >
      {name === "dashboard" ? (
        <>
          {/* The dial the dashboard is built around: a 270-degree arc open at
              the bottom, matching GrillGauge's own sweep, plus a needle. */}
          <Path d="M5.6 18.4A9 9 0 1 1 18.4 18.4" {...common} />
          <Path d="M12 12L8.4 8.4" {...common} />
        </>
      ) : null}

      {name === "history" ? (
        <>
          {/* Axes plus a plotted line -- the cook graph the screen shows. */}
          <Path d="M4 4v16h16" {...common} />
          <Path d="M7 15.5L11 10.5L14.5 13L19.5 6.5" {...common} />
        </>
      ) : null}

      {name === "events" ? (
        <>
          {/* A bell: events are the notification feed. */}
          <Path d="M12 4.4V3" {...common} />
          <Path
            d="M6.8 9.6a5.2 5.2 0 0 1 10.4 0v3.2c0 2.6 1.4 3.6 1.4 3.6H5.4s1.4-1 1.4-3.6Z"
            {...common}
          />
          <Path d="M10.2 19a1.9 1.9 0 0 0 3.6 0" {...common} />
        </>
      ) : null}

      {name === "preferences" ? (
        <>
          {/* Sliders rather than a gear: the screen is a set of individual
              choices (accent, host, units), not one settings hub. */}
          <Path d="M4 7h16" {...common} />
          <Circle cx={9} cy={7} r={2.1} {...common} />
          <Path d="M4 12h16" {...common} />
          <Circle cx={15} cy={12} r={2.1} {...common} />
          <Path d="M4 17h16" {...common} />
          <Circle cx={8} cy={17} r={2.1} {...common} />
        </>
      ) : null}
    </Svg>
  );
}
