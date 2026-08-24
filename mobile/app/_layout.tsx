import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";
import { Stack, useRouter, usePathname } from "expo-router";
import * as Notifications from "expo-notifications";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { projectProbeHealth } from "@pifire/core/dashboard/probeHealth";
import { HealthBanner } from "../src/components/HealthBanner";
import { alertsFor } from "../src/alerts";
import { loadHosts } from "../src/host";
import { defaultPrefs, loadPrefs, savePrefs, type Prefs } from "../src/prefs";
import { THEME } from "../src/theme";
import {
  LIVE_STALE_AFTER_MS,
  qualifyRetainedHealth,
  useLive,
  type LiveResult,
} from "../src/useLive";

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

const LiveContext = createContext<LiveResult | null>(null);

// Screens read the connection through this instead of calling useLive()
// directly, so the app opens exactly one socket per connected grill
// regardless of how many screens want the data.
export function useLiveContext(): LiveResult {
  const value = useContext(LiveContext);
  if (!value) {
    throw new Error("useLiveContext must be used within the connected app shell");
  }
  return value;
}

interface PrefsContextValue {
  prefs: Prefs;
  /** Publishes a newly saved host so the layout can open a connection to it.
   *  Storage alone is not enough: the tab routes are guarded on the host being
   *  known, so nothing can navigate into them until this is called. */
  setActiveHost: (host: string) => void;
  /** Merges `partial` into the current prefs, updates every consumer
   *  immediately (this is what makes an accent change apply live -- see the
   *  preferences screen), and persists the result in the background. */
  updatePrefs: (partial: Partial<Prefs>) => void;
}

const PrefsContext = createContext<PrefsContextValue | null>(null);

// Every screen colors itself from this instead of hardcoding THEME.ember, so
// a change made on the preferences screen recolors the whole running app at
// once rather than on next launch.
export function usePrefsContext(): PrefsContextValue {
  const value = useContext(PrefsContext);
  if (!value) {
    throw new Error("usePrefsContext must be used within the connected app shell");
  }
  return value;
}

function StatusStrip({ live, now }: { live: LiveResult; now: number }) {
  const { prefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];
  const ageMs = live.lastPayloadAt === null ? null : now - live.lastPayloadAt;
  const stale = ageMs === null || ageMs > LIVE_STALE_AFTER_MS;

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
    <View style={[styles.strip, { backgroundColor: tokens.surface }]}>
      <Text style={[styles.stripText, { color }]}>{label}</Text>
    </View>
  );
}

// The decision logic -- which alerts a transition warrants -- lives entirely
// in alertsFor (src/alerts.ts), which is pure and unit-tested. This effect
// only tracks the previous payload across renders and delivers its results
// when the loaded preference enables local alerts.
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
function useAlertNotifications(
  dash: DashSocketPayload,
  lastPayloadAt: number | null,
  alertsEnabled: boolean,
) {
  const previousRef = useRef<DashSocketPayload | null>(null);

  useEffect(() => {
    if (lastPayloadAt === null) {
      return;
    }
    const alerts = alertsFor(previousRef.current, dash);
    previousRef.current = dash;
    if (!alertsEnabled) {
      return;
    }
    for (const alert of alerts) {
      Notifications.scheduleNotificationAsync({
        identifier: alert.id,
        content: { title: alert.title, body: alert.body },
        trigger: null,
      });
    }
  }, [alertsEnabled, dash, lastPayloadAt]);
}

function LiveShell({
  host,
  alertsEnabled,
  children,
}: {
  host: string;
  alertsEnabled: boolean;
  children: React.ReactNode;
}) {
  const live = useLive(host);
  // One shell clock drives both the transport strip and retained health.
  // Without it, an open-but-silent socket could age the strip while every
  // health surface stayed permanently "current".
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const displayLive = useMemo(() => qualifyRetainedHealth(live, now), [live, now]);
  const primaryWire = displayLive.live.thermocoupleHealth?.find(
    (health) => health.role === "Primary" && health.label === displayLive.live.primaryProbe.label,
  );
  const primaryHealth = primaryWire ? projectProbeHealth(primaryWire) : null;
  useAlertNotifications(live.live, live.lastPayloadAt, alertsEnabled);

  useEffect(() => {
    if (!alertsEnabled) {
      return;
    }
    Notifications.requestPermissionsAsync();
    if (Platform.OS === "android") {
      Notifications.setNotificationChannelAsync("default", {
        name: "PiFire alerts",
        importance: Notifications.AndroidImportance.HIGH,
      });
    }
  }, [alertsEnabled]);

  return (
    <LiveContext.Provider value={displayLive}>
      <StatusStrip live={live} now={now} />
      <HealthBanner health={primaryHealth} />
      {children}
    </LiveContext.Provider>
  );
}

export default function RootLayout() {
  const router = useRouter();
  const pathname = usePathname();
  // undefined: storage not read yet. null: read, and empty.
  const [host, setHost] = useState<string | null | undefined>(undefined);
  const [prefs, setPrefs] = useState<Prefs>(defaultPrefs);
  const [prefsLoaded, setPrefsLoaded] = useState(false);

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

  // Loaded once at launch. Unlike host, nothing outside this provider's own
  // updatePrefs ever changes the underlying storage, so there is no
  // navigation-triggered staleness to re-read for.
  useEffect(() => {
    let cancelled = false;
    loadPrefs().then((p) => {
      if (!cancelled) {
        setPrefs(p);
        setPrefsLoaded(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (host === null) {
      router.replace({
        pathname: "/connect",
        params: { reason: "Connect to a grill to see live data." },
      });
    }
  }, [host, router]);

  // Updates the in-memory value driving every screen's colors immediately --
  // this is what makes the preferences screen's accent picker apply live --
  // and persists in the background; a change is never lost, but the UI never
  // waits on the write to reflect it.
  const updatePrefs = useCallback((partial: Partial<Prefs>) => {
    setPrefs((current) => {
      const next = { ...current, ...partial };
      void savePrefs(next);
      return next;
    });
  }, []);

  // prefs.host mirrors the `host` state above (host.ts's remembered-hosts
  // list) rather than whatever loadPrefs happened to read from storage, so
  // there is exactly one source of truth for "the active grill" and it can't
  // drift from what RootLayout itself is routing on.
  const prefsValue = useMemo<PrefsContextValue>(
    () => ({ prefs: { ...prefs, host: host ?? null }, updatePrefs, setActiveHost: setHost }),
    [prefs, host, updatePrefs],
  );

  // The tab screens call useLiveContext(), which throws when no connection is
  // provided. Guarding them at the navigator means the router will not mount
  // them at all until a host is known, so no navigation can land on the
  // dashboard before the connection exists. Doing this with a redirect
  // instead cannot work: redirects run in effects, which is after the render
  // that would already have thrown.
  const connected = typeof host === "string";
  const routes = (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Protected guard={connected}>
        <Stack.Screen name="(tabs)" />
      </Stack.Protected>
      <Stack.Screen name="connect" />
    </Stack>
  );

  if (host === undefined) {
    // Storage has not answered yet. Rendering the navigator now would show
    // the connect screen for a frame or two before bouncing to the dashboard,
    // so render nothing instead -- the native splash is still up.
    return <PrefsContext.Provider value={prefsValue}>{null}</PrefsContext.Provider>;
  }

  // Keyed by host so switching to a different remembered grill tears down the
  // old connection and its state rather than reusing it.
  return (
    <PrefsContext.Provider value={prefsValue}>
      {connected ? (
        <LiveShell
          host={host as string}
          key={host as string}
          alertsEnabled={prefsLoaded && prefs.alerts}
        >
          {routes}
        </LiveShell>
      ) : (
        routes
      )}
    </PrefsContext.Provider>
  );
}

const styles = StyleSheet.create({
  strip: {
    paddingTop: 48,
    paddingBottom: 8,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  stripText: {
    fontSize: 12,
    fontWeight: "600",
  },
});
