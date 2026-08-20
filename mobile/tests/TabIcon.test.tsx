import { cleanup, render } from "@testing-library/react-native";
import { processColor } from "react-native";
import { TabIcon, tabIconTestID, type TabIconName } from "../src/components/TabIcon";
import { TAB_SCREENS } from "../src/tabs";

// Each glyph is several sibling shapes; which of them carries the stroke is a
// drawing detail, that all of them do is the contract -- so every shape in the
// icon is collected rather than one hand-picked element.
//
// @testing-library/react-native@14's render is async (see GrillGauge.test.tsx).
type Rendered = { type?: string; props?: Record<string, unknown>; children?: unknown[] };

function shapes(node: unknown): Record<string, unknown>[] {
  if (node === null || typeof node !== "object") {
    return [];
  }
  const el = node as Rendered;
  const own = el.type?.startsWith("RNSVG") && el.type !== "RNSVGSvgView" && el.props
    ? [el.props]
    : [];
  const kids = Array.isArray(el.children) ? el.children.flatMap(shapes) : [];
  return [...own, ...kids];
}

async function shapesOf(name: TabIconName, color: string, size = 24) {
  const view = await render(<TabIcon name={name} color={color} size={size} />);
  // Drawn shapes only, minus the RNSVGGroup wrapper react-native-svg adds:
  // the group carries its own default fill and no stroke.
  return shapes(view.toJSON()).filter((p) => "stroke" in p);
}

const NAMES: TabIconName[] = ["dashboard", "history", "events", "preferences"];

describe("TabIcon", () => {
  afterEach(cleanup);

  // The failure this component exists to prevent is a tab falling back to
  // expo-router's MissingIcon, which is what a screen with no tabBarIcon gets
  // (build/react-navigation/bottom-tabs/views/BottomTabBar.js).
  it.each(NAMES)("draws a glyph for %s", async (name) => {
    expect((await shapesOf(name, "#ff8a2b")).length).toBeGreaterThan(0);
  });

  // The tab bar tints active and inactive tabs differently and passes the
  // resolved color in; a glyph that painted itself would sit at one color in
  // both states. react-native-svg processes the hex into a native color on
  // the way down, so the expectation is processed the same way.
  it.each(NAMES)("paints every shape of %s in the color it is given", async (name) => {
    const expected = processColor("#3cc7d0");
    for (const props of await shapesOf(name, "#3cc7d0")) {
      expect((props.stroke as { payload: number }).payload).toBe(expected);
      expect(props.fill).toBeNull();
    }
  });

  // .maestro/tab-icons.yaml selects each glyph by this id, and testID only
  // reaches the platform accessibility tree if react-native-svg forwards it to
  // the native view rather than swallowing it as an unknown SVG attribute.
  it.each(NAMES)("tags %s with the id the end-to-end flow selects on", async (name) => {
    const view = await render(<TabIcon name={name} color="#fff" />);
    expect(view.getByTestId(tabIconTestID(name))).toBeTruthy();
  });

  // Stroke width is expressed in viewBox units, so the same glyph drawn at
  // half the size would come out at half the apparent line weight unless it
  // is compensated for.
  it("keeps its line weight when rendered smaller", async () => {
    const full = (await shapesOf("dashboard", "#fff", 24))[0].strokeWidth as number;
    const half = (await shapesOf("dashboard", "#fff", 12))[0].strokeWidth as number;
    expect(half).toBeCloseTo(full * 2);
  });
});

describe("TAB_SCREENS", () => {
  // The layout renders one Tabs.Screen per row here, so a row naming a glyph
  // TabIcon does not draw would put that tab back on the MissingIcon
  // fallback. (That a route file exists for every row, and a row for every
  // route file, is not checked here: reading app/(tabs)/ needs node's fs, and
  // tsconfig.json deliberately admits no ambient types beyond jest's.)
  it.each(TAB_SCREENS)("draws a glyph for the $name tab", async ({ icon }) => {
    expect((await shapesOf(icon, "#ff8a2b")).length).toBeGreaterThan(0);
  });

  it("gives every screen a distinct title and glyph", () => {
    expect(new Set(TAB_SCREENS.map((s) => s.title)).size).toBe(TAB_SCREENS.length);
    expect(new Set(TAB_SCREENS.map((s) => s.icon)).size).toBe(TAB_SCREENS.length);
  });
});
