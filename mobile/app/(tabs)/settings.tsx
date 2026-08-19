import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { THEME, type AccentName } from "../../src/theme";
import { usePrefsContext } from "../_layout";

const ACCENTS: AccentName[] = ["ember", "ice", "crimson"];
const ACCENT_LABELS: Record<AccentName, string> = {
  ember: "Ember",
  ice: "Ice",
  crimson: "Crimson",
};

export default function Settings() {
  const router = useRouter();
  const { prefs, updatePrefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: tokens.background }]}
      contentContainerStyle={styles.content}
    >
      <Text style={[styles.title, { color: tokens.text }]}>Preferences</Text>

      <View style={styles.section}>
        <Text style={[styles.sectionLabel, { color: tokens.text }]}>Grill</Text>
        <Text style={[styles.hostValue, { color: tokens.text }]}>
          {prefs.host ?? "Not connected"}
        </Text>
        {/* No inline host editor here -- host.ts's normalizeHost already lives
            on the Connect screen and stays the one place a typed host string
            gets parsed. This just routes back there rather than parsing a
            second copy of the same input. */}
        <Pressable
          style={[styles.button, { backgroundColor: tokens.accent }]}
          onPress={() => router.push("/connect")}
        >
          <Text style={[styles.buttonText, { color: tokens.background }]}>Change grill</Text>
        </Pressable>
        <Text style={[styles.note, { color: tokens.text }]}>
          Grill configuration -- probes, PID tuning, GPIO pins, and the rest --
          lives in PiFire's web UI, not here.
        </Text>
      </View>

      <View style={styles.section}>
        <Text style={[styles.sectionLabel, { color: tokens.text }]}>Accent</Text>
        <View style={styles.accentRow}>
          {ACCENTS.map((accent) => {
            const selected = accent === prefs.accent;
            const accentTokens = THEME[accent];
            return (
              <Pressable
                key={accent}
                style={[
                  styles.accentSwatch,
                  {
                    backgroundColor: accentTokens.accent,
                    borderColor: selected ? tokens.text : "transparent",
                  },
                ]}
                onPress={() => updatePrefs({ accent })}
              >
                <Text style={[styles.accentLabel, { color: accentTokens.background }]}>
                  {ACCENT_LABELS[accent]}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </View>

      <View style={styles.section}>
        <View style={styles.alertsRow}>
          <Text style={[styles.sectionLabel, { color: tokens.text }]}>Local alerts</Text>
          <Switch
            value={prefs.alerts}
            onValueChange={(value) => updatePrefs({ alerts: value })}
            trackColor={{ true: tokens.accent }}
          />
        </View>
        {/* Same limitation stated on the Events screen (events.tsx), carried
            here too since this is the screen where the toggle it describes
            actually lives -- Task 15 had nowhere else to put it yet. */}
        <Text style={[styles.note, { color: tokens.text }]}>
          Local alerts only fire while PiFire is open on this phone. For
          notifications that reach you while it's closed, set up PiFire's
          server-side notification services (Apprise, Pushover, Pushbullet, or
          IFTTT) in the grill's own settings -- those remain the reliable path.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingVertical: 24,
    paddingHorizontal: 16,
    gap: 24,
  },
  title: {
    fontSize: 20,
    fontWeight: "700",
  },
  section: {
    gap: 10,
  },
  sectionLabel: {
    fontSize: 13,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  hostValue: {
    fontSize: 16,
  },
  button: {
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonText: {
    fontWeight: "600",
    fontSize: 15,
  },
  note: {
    fontSize: 12,
    lineHeight: 17,
    opacity: 0.7,
  },
  accentRow: {
    flexDirection: "row",
    gap: 10,
  },
  accentSwatch: {
    flex: 1,
    borderRadius: 10,
    borderWidth: 2,
    paddingVertical: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  accentLabel: {
    fontWeight: "700",
    fontSize: 13,
  },
  alertsRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
});
