import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { THEME } from "../../src/theme";
import { useLiveContext, usePrefsContext } from "../_layout";

// Same endpoint web-react's own Events tab reads (web-react/src/components/
// logs/EventsPage.tsx -> LogViewer stem="events" -> helpers/logs/logsApi.ts's
// logViewUrl, hitting blueprints/api_admin/routes.py's
// `GET /api/admin/logs/view?log=<stem>`). It answers with the stitched events
// log as plain text (one line per event, oldest first) rather than the JSON
// envelope most of this app's other reads use -- there is no separate
// structured events API; this admin log view is the one PiFire already
// serves.
const EVENTS_LOG_STEM = "events";

// How many of the most recent lines to keep after fetching the whole log.
// The log can run to hundreds of entries; a phone screen has room for a
// couple of dozen without turning this into another log viewer.
const MAX_EVENTS = 40;

function parseEventLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .slice(-MAX_EVENTS)
    .reverse();
}

export default function Events() {
  const { host } = useLiveContext();
  const { prefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];
  const [lines, setLines] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(
    async (opts: { showSpinner: boolean }) => {
      if (opts.showSpinner) setRefreshing(true);
      try {
        const res = await fetch(`${host}/api/admin/logs/view?log=${EVENTS_LOG_STEM}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        setLines(parseEventLines(text));
        setError(null);
      } catch {
        setError("Could not load events from the grill.");
      } finally {
        if (opts.showSpinner) setRefreshing(false);
      }
    },
    [host],
  );

  useEffect(() => {
    load({ showSpinner: false });
  }, [load]);

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: tokens.background }]}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => load({ showSpinner: true })}
          tintColor={tokens.accent}
        />
      }
    >
      <Text style={[styles.title, { color: tokens.text }]}>Events</Text>

      {/* The limitation this screen and its alerts share: they only work
          while this app is open. Placed here, not buried in a settings
          screen, because this is where someone configuring "will my phone
          tell me" actually looks. Also stated on the preferences screen
          (settings.tsx), next to the alerts toggle it's really about. */}
      <Text style={[styles.notice, { color: tokens.text }]}>
        Alerts on this screen only fire while PiFire is open on this phone. For notifications that
        reach you while it's closed, set up PiFire's server-side notification services (Apprise,
        Pushover, Pushbullet, or IFTTT) in the grill's own settings -- those remain the reliable
        path.
      </Text>

      {lines === null && error === null ? (
        <ActivityIndicator color={tokens.accent} style={styles.spinner} />
      ) : null}

      {error !== null ? (
        <Text style={[styles.error, { color: tokens.danger }]}>{error}</Text>
      ) : null}

      {lines !== null && lines.length === 0 ? (
        <Text style={[styles.empty, { color: tokens.text }]}>No events yet.</Text>
      ) : null}

      {lines !== null && lines.length > 0 ? (
        <View style={styles.list}>
          {lines.map((line, i) => (
            <Text key={i} style={[styles.line, { color: tokens.text }]}>
              {line}
            </Text>
          ))}
        </View>
      ) : null}
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
    gap: 16,
  },
  title: {
    fontSize: 20,
    fontWeight: "700",
  },
  notice: {
    // THEME has no separate "muted label" token for chrome text (see
    // src/theme.ts's GAUGE_ACCENT/LABEL_COLOR, which are gauge-specific) --
    // dimming the ordinary text color is this file's own choice, not a
    // ported value.
    opacity: 0.7,
    fontSize: 12,
    lineHeight: 17,
  },
  spinner: {
    marginTop: 32,
  },
  error: {
    fontSize: 14,
  },
  empty: {
    opacity: 0.7,
    fontSize: 14,
  },
  list: {
    gap: 6,
  },
  line: {
    fontSize: 12,
    fontFamily: "monospace",
  },
});
