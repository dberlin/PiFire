import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { addProfile, deleteLog, editBrands, hopperCheck, loadProfile } from "./pelletsApi";

const fetchMock = rs.fn();
rs.stubGlobal("fetch", fetchMock);

function ok(body: unknown) {
  return { ok: true, json: async () => body };
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("pelletsApi", () => {
  it("posts an intent, never a database", async () => {
    fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
    await editBrands("", { new_brand: "Acme" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/pellets");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      action: "edit_brands",
      data: { new_brand: "Acme" },
    });
  });

  it("treats result OK as success", async () => {
    fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
    expect(await hopperCheck("")).toEqual({ ok: true, message: "" });
  });

  it("surfaces the server's Error message", async () => {
    fetchMock.mockResolvedValue(
      ok({ result: "Error", message: "Error: Cannot delete current profile", data: null }),
    );
    expect(await loadProfile("", "abc")).toEqual({
      ok: false,
      message: "Error: Cannot delete current profile",
    });
  });

  it("reports a non-2xx as an HTTP failure without parsing", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    expect(await deleteLog("", "2026-07-25 10:00:00")).toEqual({
      ok: false,
      message: "HTTP 503",
    });
  });

  it("reports a thrown fetch as a network failure", async () => {
    fetchMock.mockRejectedValue(new Error("boom"));
    expect(await hopperCheck("")).toEqual({ ok: false, message: "boom" });
  });

  it("sends add_and_load with add_profile", async () => {
    fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
    await addProfile("", {
      brand_name: "Generic",
      wood_type: "Oak",
      rating: 4,
      comments: "c",
      add_and_load: true,
    });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).data.add_and_load).toBe(true);
  });
});
