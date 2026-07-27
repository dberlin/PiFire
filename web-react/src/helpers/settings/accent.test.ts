import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { accentPath, readAccent, saveAccent, storedAccentName } from "./accent";
import type { Settings } from "./settingsApi";

// The helpers take a whole Settings; these fixtures carry only the keys under
// test, which is why each is cast rather than spread over a full default tree.
const partial = (s: object) => s as Settings;

const withDisplay = (accent?: string) =>
  partial({
    modules: { display: "qtquick_flex" },
    display: { config: { qtquick_flex: accent === undefined ? {} : { accent_theme: accent } } },
  });

afterEach(() => {
  rs.restoreAllMocks();
});

describe("accentPath", () => {
  it("addresses the selected display module", () => {
    expect(accentPath(withDisplay("Ice"))).toBe("display.config.qtquick_flex.accent_theme");
  });

  // Before the wizard runs there is no selected display module, so there is
  // nowhere to put an accent. Callers must not invent a path.
  it("is null when no display module is selected", () => {
    expect(accentPath(partial({}))).toBeNull();
    expect(accentPath(partial({ modules: {} }))).toBeNull();
  });
});

describe("readAccent", () => {
  it("lowercases the stored spelling", () => {
    expect(readAccent(withDisplay("Ice"))).toBe("ice");
    expect(readAccent(withDisplay("Crimson"))).toBe("crimson");
    expect(readAccent(withDisplay("Ember"))).toBe("ember");
  });

  it("falls back to ember for absent, empty and unrecognised values", () => {
    expect(readAccent(partial({}))).toBe("ember");
    expect(readAccent(withDisplay())).toBe("ember");
    expect(readAccent(withDisplay("Chartreuse"))).toBe("ember");
  });
});

describe("storedAccentName", () => {
  it("restores the spelling Theme.qml uses", () => {
    expect(storedAccentName("ember")).toBe("Ember");
    expect(storedAccentName("ice")).toBe("Ice");
    expect(storedAccentName("crimson")).toBe("Crimson");
  });
});

describe("saveAccent", () => {
  it("reads the module, then writes the accent under it with no control flags", async () => {
    const calls: { url: string; body: unknown }[] = [];
    rs.spyOn(globalThis, "fetch").mockImplementation((async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
      const payload = init?.method === "POST" ? { result: "success" } : withDisplay("Ember");
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch);

    expect(await saveAccent("", "crimson")).toBe(true);

    const post = calls.find((c) => c.url.includes("settings_update"));
    expect(post?.body).toEqual({
      settings: { display: { config: { qtquick_flex: { accent_theme: "Crimson" } } } },
      flags: [],
    });
  });

  it("reports failure rather than throwing when there is no display module", async () => {
    rs.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ modules: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    expect(await saveAccent("", "ice")).toBe(false);
  });

  // The caller has already applied the accent locally, so a write that cannot
  // reach the backend must cost the persistence and nothing else.
  it("reports failure rather than throwing when the fetch rejects", async () => {
    rs.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));

    expect(await saveAccent("", "ice")).toBe(false);
  });
});
