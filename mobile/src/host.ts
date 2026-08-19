import AsyncStorage from "@react-native-async-storage/async-storage";

// The single place a typed host string becomes an API base URL.
//
// Port 5000 is not arbitrary: it is what gunicorn binds in
// auto-install/supervisor/webapp.conf, so a bare hostname (the common case,
// e.g. "pifire.local") gets that port by default. A trailing slash is
// always stripped so the command client never builds a doubled path like
// "http://pi:5000//api/...".
export function normalizeHost(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed.length === 0) {
    return null;
  }

  // Add a scheme if the input didn't specify one, so the URL constructor
  // below can parse host/port the same way for both cases.
  const withScheme = /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;

  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    return null;
  }

  if (!url.hostname) {
    return null;
  }

  // Default port 5000 only when the input didn't specify one.
  const port = url.port || "5000";

  // Strip any path/trailing slash — normalizeHost produces an API base,
  // not a full URL.
  return `${url.protocol}//${url.hostname}:${port}`;
}

const HOSTS_KEY = "pifire.hosts";
const MAX_HOSTS = 5;

// The remembered-hosts list, most-recent-first. The head of the list is the
// active host.
export async function loadHosts(): Promise<string[]> {
  const raw = await AsyncStorage.getItem(HOSTS_KEY);
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// Moves `url` to the front of the remembered-hosts list (deduplicated),
// persists it, and returns the updated list, capped at MAX_HOSTS entries.
export async function rememberHost(url: string): Promise<string[]> {
  const existing = await loadHosts();
  const deduped = [url, ...existing.filter((h) => h !== url)].slice(0, MAX_HOSTS);
  await AsyncStorage.setItem(HOSTS_KEY, JSON.stringify(deduped));
  return deduped;
}
