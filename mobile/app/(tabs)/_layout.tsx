import { Tabs } from "expo-router";
import { THEME } from "../../src/theme";
import { usePrefsContext } from "../_layout";

// The tab bar for the four screens reachable once a grill is connected:
// dashboard (index.tsx), history, events, and preferences (settings.tsx).
// Route names below are the actual filenames in this (tabs) group -- there
// is no separate route-name mapping to keep in sync.
//
// Colored from the live accent (usePrefsContext, see its doc comment in
// ../_layout.tsx) so switching accents on the preferences screen recolors
// the tab bar immediately, the same as everything else that reads from it.
export default function TabsLayout() {
  const { prefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: tokens.accent,
        tabBarInactiveTintColor: tokens.text,
        tabBarStyle: {
          backgroundColor: tokens.surface,
          borderTopColor: tokens.surface,
        },
      }}
    >
      <Tabs.Screen name="index" options={{ title: "Dashboard" }} />
      <Tabs.Screen name="history" options={{ title: "History" }} />
      <Tabs.Screen name="events" options={{ title: "Events" }} />
      <Tabs.Screen name="settings" options={{ title: "Preferences" }} />
    </Tabs>
  );
}
