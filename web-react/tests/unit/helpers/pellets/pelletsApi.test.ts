import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  addProfile,
  deleteLog,
  deleteProfile,
  editBrands,
  editProfile,
  editWoods,
  hopperCheck,
  loadProfile,
} from "../../../../src/helpers/pellets/pelletsApi";

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

  // Every helper is a one-liner wrapping post() with a literal action name, so
  // the only mistake available is a copy-pasted name -- and the server routes
  // purely on that string (PELLETS_DISPATCH, common/pellets_actions.py:171-180).
  // A wrong-but-valid name runs a DIFFERENT handler, which then finds none of
  // its own keys and answers "Error: Function not specified"; a name that is
  // not in the map at all is rejected outright. Neither is visible from the
  // React side, where these are only ever referenced by function identity.
  it("names each action exactly as PELLETS_DISPATCH keys it", async () => {
    const fields = { brand_name: "Generic", wood_type: "Oak", rating: 4, comments: "c" };
    const cases: [string, () => Promise<unknown>][] = [
      ["load_profile", () => loadProfile("", "p1")],
      ["hopper_check", () => hopperCheck("")],
      ["edit_brands", () => editBrands("", { new_brand: "Acme" })],
      ["edit_woods", () => editWoods("", { new_wood: "Hickory" })],
      ["add_profile", () => addProfile("", { ...fields, add_and_load: false })],
      ["edit_profile", () => editProfile("", { ...fields, profile: "p1" })],
      ["delete_profile", () => deleteProfile("", "p1")],
      ["delete_log", () => deleteLog("", "2026-07-25 09:00:00")],
    ];

    for (const [action, call] of cases) {
      fetchMock.mockReset();
      fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
      await call();
      expect(JSON.parse(fetchMock.mock.calls[0][1].body).action).toBe(action);
    }
  });

  it("carries the profile id alongside the fields on edit_profile", async () => {
    // pellets_edit_profile:132 subscripts archive[action_data["profile"]]; the
    // id travels in the SAME flat object as the fields, not nested under them.
    fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
    await editProfile("", {
      profile: "p1",
      brand_name: "Generic",
      wood_type: "Oak",
      rating: 4,
      comments: "c",
    });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).data).toEqual({
      profile: "p1",
      brand_name: "Generic",
      wood_type: "Oak",
      rating: 4,
      comments: "c",
    });
  });
});
