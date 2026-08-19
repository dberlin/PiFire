import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";
import { Stack, useRouter, usePathname } from "expo-router";
import * as Notifications from "expo-notifications";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { alertsFor } from "../src/alerts";
import { loadHosts } from "../src/host";
import { THEME } from "../src/theme";
import { useLive, type LiveResult } from "../src/useLive";

const tokens = THEME.ember;

// Foreground behavior: expo-notifications' default handler does not present a
// notification while the app is open, and a local alert that only shows once
// the user has switched away defeats the "brisket hit temperature" use case,
// which is watched for from inside the app. Module scope, not an effect, so
// it is registered exactly once for the process's lifetime rather than
// re-registered on every LiveShell mount.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// A payload older than this is not "current" -- see useLive's
// lastPayloadAt doc comment. A phone that sat in a pocket for ten minutes
// must not look live just because the socket is still technically open.
const STALE_AFTER_MS = 30_000;

const LiveContext = createContext<LiveResult | null>(null);

// Tasks 12-13's screens read the connection through this instead of
// calling useLive() directly, so the app opens exactly one socket per
// connected grill regardless of how many screens want the data.
export function useLiveContext(): LiveResult {
  const value = useContext(LiveContext);
  if (!value) {
    throw new Error("useLiveContext must be used within the connected app shell");
  }
  return value;
}

function StatusStrip({ live }: { live: LiveResult }) {
  // Ticks once a second purely to re-render this label -- lastPayloadAt
  // itself only changes when a new dash payload arrives, but the reported
  // age needs to keep counting up between payloads.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const ageMs = live.lastPayloadAt === null ? null : now - live.lastPayloadAt;
  const stale = ageMs === null || ageMs > STALE_AFTER_MS;

  let label: string;
  let color: string = tokens.text;
  if (live.phase === "connecting") {
    label = "Connecting…";
  } else if (live.phase === "unreachable") {
    label = "Unreachable";
    color = tokens.danger;
  } else if (stale) {
    // Covers "live" (socket connected) with no fresh payload yet, or one
    // that stopped arriving -- the socket being open is not evidence the
    // reading on screen is current.
    label = ageMs === null ? "No data yet" : `Stale · ${Math.floor(ageMs / 1000)}s ago`;
    color = tokens.danger;
  } else {
    label = "Live";
    color = tokens.accent;
  }

  return (
    <View style={styles.strip}>
      <Text style={[styles.stripText, { color }]}>{label}</Text>
    </View>
  );
}

// The decision logic -- which alerts a transition warrants -- lives entirely
// in alertsFor (src/alerts.ts), which is pure and unit-tested. This effect
// contains none of it: it only tracks the previous payload across renders and
// hands whatever alertsFor returns to expo-notifications.
//
// `previousRef` starts at null and is compared against with alertsFor's own
// null-previous rule (see that file's doc comment) -- that is what keeps the
// very first real payload after launch from alerting on state the app just
// discovered rather than state that just changed.
//
// `lastPayloadAt` (not `dash` alone) gates whether a payload is real: useLive
// seeds its `live` state with FIXTURE_DASH before the socket has delivered
// anything, and that placeholder must not be treated as "the first payload"
// -- it would make the *actual* first real payload look like a transition
// away from FIXTURE_DASH's all-zero state instead of the true first payload,
// which is exactly the false-alert-on-launch failure mode this whole function
// exists to avoid.
function useAlertNotifications(dash: DashSocketPayload, lastPayloadAt: number | null) {
  const previousRef = useRef<DashSocketPayload | null>(null);

  useEffect(() => {
    if (lastPayloadAt === null) {
      return;
    }
    for (const alert of alertsFor(previousRef.current, dash)) {
      Notifications.scheduleNotificationAsync({
        identifier: alert.id,
        content: { title: alert.title, body: alert.body },
        trigger: null,
      });
    }
    previousRef.current = dash;
  }, [dash, lastPayloadAt]);
}

function LiveShell({ host }: { host: string }) {
  const live = useLive(host);
  useAlertNotifications(live.live, live.lastPayloadAt);

  // Fire-and-forget: this is what actually raises the OS permission prompt
  // on iOS. A denial just means scheduleNotificationAsync's notifications
  // never surface -- nothing here depends on the result.
  useEffect(() => {
    Notifications.requestPermissionsAsync();
    if (Platform.OS === "android") {
      Notifications.setNotificationChannelAsync("default", {
        name: "PiFire alerts",
        importance: Notifications.AndroidImportance.HIGH,
      });
    }
  }, []);

  return (
    <LiveContext.Provider value={live}>
      <StatusStrip live={live} />
      <Stack />
    </LiveContext.Provider>
  );
}

export default function RootLayout() {
  const router = useRouter();
  const pathname = usePathname();
  // undefined: storage not read yet. null: read, and empty.
  const [host, setHost] = useState<string | null | undefined>(undefined);

  // Re-read on every navigation, not just on mount: this layout stays
  // mounted for the app's whole lifetime, and connect.tsx saves a new host
  // and replaces to "/" rather than remounting anything above it.
  useEffect(() => {
    let cancelled = false;
    loadHosts().then((hosts) => {
      if (!cancelled) {
        setHost(hosts[0] ?? null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  useEffect(() => {
    if (host === null) {
      router.replace({
        pathname: "/connect",
        params: { reason: "Connect to a grill to see live data." },
      });
    }
  }, [host, router]);

  if (!host) {
    // Loading, or nothing configured yet (the effect above is sending the
    // user to /connect in the latter case) -- nothing to open a
    // connection to, so no status strip or live context either.
    return <Stack />;
  }

  // Keyed by host so switching to a different remembered grill tears down
  // the old connection and its state rather than reusing it.
  return <LiveShell host={host} key={host} />;
}

const styles = StyleSheet.create({
  strip: {
    paddingTop: 48,
    paddingBottom: 8,
    paddingHorizontal: 16,
    alignItems: "center",
    backgroundColor: tokens.surface,
  },
  stripText: {
    fontSize: 12,
    fontWeight: "600",
  },
});
