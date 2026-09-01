import { Tabs } from "expo-router";

import { TabIcon } from "../../src/components/TabIcon";
import { TAB_SCREENS } from "../../src/tabs";
import { THEME } from "../../src/theme";
import { usePrefsContext } from "../_layout";

// The tab bar for the four screens reachable once a grill is connected:
// dashboard (index.tsx), history, events, and preferences (settings.tsx).
// Which screens those are, and the glyph each one carries, is TAB_SCREENS
// (../../src/tabs.ts).
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
      {TAB_SCREENS.map(({ name, title, icon }) => (
        <Tabs.Screen
          key={name}
          name={name}
          options={{
            title,
            tabBarIcon: ({ color, size }) => <TabIcon name={icon} color={color} size={size} />,
          }}
        />
      ))}
    </Tabs>
  );
}
