import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { queryClient } from "../../../../src/helpers/query/queryClient";
import {
  accentPath,
  readAccent,
  saveAccent,
  storedAccentName,
} from "../../../../src/helpers/settings/accent";
import type { SettingsSchema } from "../../../../src/helpers/settings/settingsTypes.gen";

// The helpers take a whole Settings; these fixtures carry only the keys under
// test, which is why each is cast rather than spread over a full default tree.
const partial = (s: object) => s as SettingsSchema;

const withDisplay = (accent?: string) =>
  partial({
    modules: { display: "qtquick_flex" },
    display: { config: { qtquick_flex: accent === undefined ? {} : { accent_theme: accent } } },
  });

beforeEach(() => {
  queryClient.clear();
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
      const payload =
        init?.method === "POST" ? { result: "success" } : { settings: withDisplay("Ember") };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch);

    expect(await saveAccent("", "crimson", queryClient)).toBe(true);

    const post = calls.find((c) => c.url.includes("settings_update"));
    expect(post?.body).toEqual({
      settings: { display: { config: { qtquick_flex: { accent_theme: "Crimson" } } } },
      flags: [],
    });
  });

  it("reports failure rather than throwing when there is no display module", async () => {
    rs.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ settings: { modules: {} } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    expect(await saveAccent("", "ice", queryClient)).toBe(false);
  });

  // The caller has already applied the accent locally, so a write that cannot
  // reach the backend must cost the persistence and nothing else.
  it("reports failure rather than throwing when the fetch rejects", async () => {
    rs.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));

    expect(await saveAccent("", "ice", queryClient)).toBe(false);
  });

  it("invalidates the empty-origin settings entry so other readers see the new accent", async () => {
    queryClient.setQueryData(queryKeys.settings(""), { modules: { display: "ili9341" } });
    rs.spyOn(globalThis, "fetch").mockImplementation((async (_url: string, init?: RequestInit) => {
      const payload =
        init?.method === "POST" ? { result: "success" } : { settings: withDisplay("Ember") };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch);

    expect(await saveAccent("", "crimson", queryClient)).toBe(true);
    expect(queryClient.getQueryState(queryKeys.settings(""))?.isInvalidated).toBe(true);
  });

  it("leaves the cache alone when the write is refused", async () => {
    queryClient.setQueryData(queryKeys.settings(""), { modules: { display: "ili9341" } });
    rs.spyOn(globalThis, "fetch").mockImplementation((async (_url: string, init?: RequestInit) => {
      const payload =
        init?.method === "POST"
          ? { result: "error", message: "no" }
          : { settings: withDisplay("Ember") };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch);

    expect(await saveAccent("", "crimson", queryClient)).toBe(false);
    expect(queryClient.getQueryState(queryKeys.settings(""))?.isInvalidated).toBe(false);
  });
});
