import type { TabIconName } from "./components/TabIcon";

// The tab bar's contents, kept out of app/(tabs)/_layout.tsx so it can be
// checked against the route files on disk without importing expo-router.
//
// `name` is the actual filename in the (tabs) group -- there is no separate
// route-name mapping to keep in sync. Every screen needs its `icon`: React
// Navigation substitutes a placeholder "missing icon" glyph for any tab that
// supplies no tabBarIcon.
export interface TabScreen {
  name: string;
  title: string;
  icon: TabIconName;
}

export const TAB_SCREENS: TabScreen[] = [
  { name: "index", title: "Dashboard", icon: "dashboard" },
  { name: "history", title: "History", icon: "history" },
  { name: "events", title: "Events", icon: "events" },
  { name: "settings", title: "Preferences", icon: "preferences" },
];
