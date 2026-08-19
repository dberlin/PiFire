import AsyncStorage from "@react-native-async-storage/async-storage";

// The single place a typed host string becomes an API base URL.
//
// No port is added by default. A standard install's real topology is nginx
// listening on 80/443 (auto-install/nginx/pifire.nginx, installed by
// auto-install/install-debian.sh) reverse-proxying to gunicorn bound to
// 127.0.0.1:8000 (auto-install/supervisor/webapp.conf) — including
// /socket.io. A bare hostname (the common case, e.g. "pifire.local") must
// therefore resolve to plain "http://pifire.local" (port 80), not a
// hardcoded port that would miss the proxy entirely. Port 5000 only shows
// up in the manual dev invocation documented in web-react/README.md, so an
// explicitly typed port (e.g. "pifire.local:5000") is always preserved
// exactly, letting a developer reach that dev backend directly. A trailing
// slash is always stripped so the command client never builds a doubled
// path like "http://pi//api/...".
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

  // Reject userinfo (e.g. "pi@fire.local"): the URL parser treats
  // everything before "@" as credentials and silently connects to the host
  // after it instead — a fat-fingered "@" must not connect somewhere the
  // user never typed.
  if (url.username || url.password) {
    return null;
  }

  // Strip any path/trailing slash — normalizeHost produces an API base,
  // not a full URL. No default port: omit it entirely unless the input
  // specified one.
  return url.port
    ? `${url.protocol}//${url.hostname}:${url.port}`
    : `${url.protocol}//${url.hostname}`;
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
