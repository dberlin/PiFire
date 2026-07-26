import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { fetchFileListing, thumbnailUrl } from "./filesApi";

afterEach(() => {
  rs.resetAllMocks();
});

const EMPTY = { items: [], page: 1, last_page: 1, per_page: 10, reverse: true, total: 0 };

function mockFetch(body: unknown, ok = true, status = 200): void {
  globalThis.fetch = rs.fn().mockResolvedValue({ ok, status, json: async () => body }) as never;
}

function firstUrl(): string {
  return String((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]);
}

describe("filesApi", () => {
  test("sends the Flask page's default window", async () => {
    mockFetch(EMPTY);
    await fetchFileListing("cookfiles");

    const url = firstUrl();
    expect(url).toContain("/api/files/cookfiles?");
    expect(url).toContain("page=1");
    expect(url).toContain("per_page=10");
    expect(url).toContain("reverse=true");
  });

  test("passes an explicit window through", async () => {
    mockFetch({ items: [], page: 3, last_page: 5, per_page: 25, reverse: false, total: 120 });
    const listing = await fetchFileListing("recipes", { page: 3, perPage: 25, reverse: false });

    const url = firstUrl();
    expect(url).toContain("/api/files/recipes?");
    expect(url).toContain("page=3");
    expect(url).toContain("per_page=25");
    expect(url).toContain("reverse=false");
    expect(listing.page).toBe(3);
  });

  test("prefixes a base url when one is configured", async () => {
    mockFetch(EMPTY);
    await fetchFileListing("cookfiles", { baseUrl: "http://pifire.local" });
    expect(firstUrl().startsWith("http://pifire.local/api/files/cookfiles?")).toBe(true);
  });

  test("throws on a non-ok response rather than returning a half-listing", async () => {
    mockFetch({}, false, 400);
    await expect(fetchFileListing("cookfiles")).rejects.toThrow("HTTP 400");
  });

  test("thumbnailUrl prefixes a relative thumbnail and falls back when empty", () => {
    expect(thumbnailUrl("abc-123/thumb.png", "")).toBe("/static/img/tmp/abc-123/thumb.png");
    expect(thumbnailUrl("", "")).toBe("/static/img/pifire-cf-thumb.png");
  });
});
