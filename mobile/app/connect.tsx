import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { loadHosts, normalizeHost, rememberHost } from "../src/host";
import { THEME } from "../src/theme";
import { usePrefsContext } from "./_layout";

export default function Connect() {
  const router = useRouter();
  const { reason } = useLocalSearchParams<{ reason?: string }>();
  const { prefs } = usePrefsContext();
  const tokens = THEME[prefs.accent];

  const [host, setHost] = useState("pifire.local");
  const [hosts, setHosts] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // On mount, prefer the most recently remembered host over the
  // "pifire.local" default.
  useEffect(() => {
    let cancelled = false;
    loadHosts().then((stored) => {
      if (cancelled) {
        return;
      }
      setHosts(stored);
      if (stored.length > 0) {
        setHost(stored[0]);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleConnect(candidate?: string) {
    const normalized = normalizeHost(candidate ?? host);
    if (!normalized) {
      setError("Enter a valid host, e.g. pifire.local or http://10.0.0.5:5000");
      return;
    }

    setError(null);
    setConnecting(true);
    try {
      const updated = await rememberHost(normalized);
      setHosts(updated);
      // "/" resolves to app/(tabs)/index.tsx -- the real dashboard, since
      // Task 13. Route groups like "(tabs)" don't add a URL segment, so
      // this is unchanged from when "/" pointed at the placeholder screen.
      router.replace("/");
    } finally {
      setConnecting(false);
    }
  }

  return (
    <View style={[styles.container, { backgroundColor: tokens.background }]}>
      <Text style={[styles.title, { color: tokens.text }]}>Connect to your grill</Text>

      {typeof reason === "string" && reason.length > 0 ? (
        <Text style={[styles.reason, { color: tokens.danger }]}>{reason}</Text>
      ) : null}

      <TextInput
        style={[
          styles.input,
          { borderColor: tokens.surface, backgroundColor: tokens.surface, color: tokens.text },
        ]}
        value={host}
        onChangeText={(text) => {
          setHost(text);
          setError(null);
        }}
        placeholder="pifire.local"
        placeholderTextColor={tokens.text}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        returnKeyType="go"
        onSubmitEditing={() => handleConnect()}
      />

      {error ? <Text style={[styles.error, { color: tokens.danger }]}>{error}</Text> : null}

      {hosts.length > 0 ? (
        <View style={styles.hostList}>
          {hosts.map((h) => (
            <Pressable
              key={h}
              style={[styles.hostOption, { backgroundColor: tokens.surface }]}
              onPress={() => {
                setHost(h);
                handleConnect(h);
              }}
            >
              <Text style={[styles.hostOptionText, { color: tokens.text }]}>{h}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      <Pressable
        style={[styles.button, { backgroundColor: tokens.accent }]}
        onPress={() => handleConnect()}
        disabled={connecting}
      >
        {connecting ? (
          <ActivityIndicator color={tokens.background} />
        ) : (
          <Text style={[styles.buttonText, { color: tokens.background }]}>Connect</Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
    gap: 16,
  },
  title: {
    fontSize: 22,
    fontWeight: "600",
    textAlign: "center",
    marginBottom: 8,
  },
  reason: {
    textAlign: "center",
    marginBottom: 8,
  },
  input: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
  error: {
    fontSize: 14,
  },
  hostList: {
    gap: 8,
  },
  hostOption: {
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  hostOptionText: {
    fontSize: 14,
  },
  button: {
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonText: {
    fontWeight: "600",
    fontSize: 16,
  },
});
