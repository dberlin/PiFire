import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { FileRequestError } from "./apiEnvelope";
import {
  createRecipe,
  deleteRecipe,
  deleteRecipeAssets,
  deleteStep,
  fetchRecipeDetail,
  insertStep,
  recipeDownloadUrl,
  runRecipe,
  setRecipeAssets,
  updateStep,
  uploadRecipe,
  uploadRecipeAssets,
} from "./recipeApi";

afterEach(() => {
  rs.resetAllMocks();
});

const DETAIL = {
  filename: "Ribs.recipe",
  metadata: {
    author: "Danny",
    username: "dberlin",
    id: "abc",
    title: "Ribs",
    description: "",
    image: "",
    thumbnail: "",
    units: "F",
    prep_time: 30,
    cook_time: 240,
    rating: 5,
    difficulty: "medium",
    version: "1.0.0",
    food_probes: 1,
  },
  recipe: { ingredients: [], instructions: [], steps: [] },
  assets: [],
};

function mockFetch(response: unknown): void {
  globalThis.fetch = rs.fn().mockResolvedValue(response) as never;
}

function calls(): unknown[][] {
  return (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls;
}

describe("recipeApi reads", () => {
  test("fetchRecipeDetail returns the parsed body and encodes the name", async () => {
    mockFetch({ ok: true, status: 200, json: async () => DETAIL });
    const detail = await fetchRecipeDetail("Sunday Ribs #2.recipe", "");

    expect(String(calls()[0][0])).toBe(
      "/api/files/recipes/detail?file=Sunday%20Ribs%20%232.recipe",
    );
    expect(detail.metadata.title).toBe("Ribs");
  });
});

describe("recipeApi writes", () => {
  test("createRecipe posts an empty body and returns the server's filename", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({ result: "OK", data: { filename: "New.recipe" } }),
    });
    const data = await createRecipe("");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/create");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({});
    expect(data.filename).toBe("New.recipe");
  });

  test("deleteRecipe posts JSON and unwraps the envelope", async () => {
    mockFetch({ ok: true, status: 200, json: async () => ({ result: "OK", data: null }) });
    await deleteRecipe("Old.recipe", "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/delete");
    expect(JSON.parse(String(init.body))).toEqual({ file: "Old.recipe" });
  });

  test("runRecipe posts the file and surfaces a 409 as a FileRequestError", async () => {
    mockFetch({
      ok: false,
      status: 409,
      json: async () => ({ result: "Error", message: "not_stopped", data: null }),
    });
    const failure = await runRecipe("X.recipe", "").catch((err) => err);

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/run");
    expect(JSON.parse(String(init.body))).toEqual({ file: "X.recipe" });
    expect(failure).toBeInstanceOf(FileRequestError);
    expect((failure as FileRequestError).detail).toEqual({
      status: 409,
      message: "not_stopped",
      errortype: null,
    });
  });
});

describe("recipeApi uploads", () => {
  test("uploadRecipe sends the archive under the `recipe` field", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({ result: "OK", data: { filename: "sanitised.recipe" } }),
    });
    const archive = new File([new Uint8Array([1])], "../../hostile.recipe");
    const filename = await uploadRecipe(archive, "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/upload");
    const form = init.body as FormData;
    expect(form.get("recipe")).toBe(archive);
    expect(filename).toBe("sanitised.recipe");
  });
});

describe("recipeApi urls", () => {
  test("recipeDownloadUrl percent-encodes a name with a space", () => {
    expect(recipeDownloadUrl("Sunday Ribs.recipe", "")).toBe(
      "/api/files/recipes/download?file=Sunday%20Ribs.recipe",
    );
  });
});

// The step and asset writers all funnel through one endpoint each and are
// distinguished only by the payload they send, so the payload IS the contract
// with recipes_api.py. These pin that shape at this end of the seam.
const okEnvelope = (data: unknown = null) => ({
  ok: true,
  status: 200,
  json: async () => ({ result: "OK", data }),
});

const STEP = {
  mode: "Hold",
  hold_temp: 225,
  timer: 0,
  notify: true,
  message: "",
  pause: false,
  trigger_temps: { primary: 0, food: [203] },
};

describe("recipeApi step writes", () => {
  test("insertStep names the insert action and its index", async () => {
    mockFetch(okEnvelope());
    await insertStep("Ribs.recipe", 2, "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/steps");
    expect(JSON.parse(String(init.body))).toEqual({
      file: "Ribs.recipe",
      action: "insert",
      index: 2,
    });
  });

  test("updateStep sends the whole step, keeping 0 sentinels intact", async () => {
    mockFetch(okEnvelope());
    await updateStep("Ribs.recipe", 1, STEP, "");

    const body = JSON.parse(String((calls()[0] as [string, RequestInit])[1].body));
    expect(body).toEqual({ file: "Ribs.recipe", action: "update", index: 1, step: STEP });
    // 0 is the disabled sentinel, a legal value -- it must survive
    // serialization rather than being dropped as falsy.
    expect(body.step.timer).toBe(0);
    expect(body.step.trigger_temps.primary).toBe(0);
  });

  test("deleteStep names the delete action and its index", async () => {
    mockFetch(okEnvelope());
    await deleteStep("Ribs.recipe", 0, "");

    const body = JSON.parse(String((calls()[0] as [string, RequestInit])[1].body));
    expect(body).toEqual({ file: "Ribs.recipe", action: "delete", index: 0 });
  });

  test("a rejected step write surfaces as a FileRequestError", async () => {
    mockFetch({
      ok: false,
      status: 400,
      json: async () => ({ result: "Error", message: "trigger_temps", data: null }),
    });
    const failure = await updateStep("Ribs.recipe", 0, STEP, "").catch((err) => err);

    expect(failure).toBeInstanceOf(FileRequestError);
    expect(failure.detail.status).toBe(400);
    // The field name is how the step editor knows which input to mark.
    expect(failure.detail.message).toBe("trigger_temps");
  });
});

describe("recipeApi asset writes", () => {
  test("uploadRecipeAssets posts multipart with every image under `assets`", async () => {
    mockFetch(okEnvelope({ assets: [{ id: "a1", filename: "a.jpg", type: "image/jpeg" }] }));
    const images = [new File(["1"], "a.jpg"), new File(["2"], "b.jpg")];
    const assets = await uploadRecipeAssets("Ribs.recipe", images, "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/assets/upload");
    const form = init.body as FormData;
    expect(form.get("file")).toBe("Ribs.recipe");
    expect(form.getAll("assets")).toHaveLength(2);
    // No JSON Content-Type may be set, or the multipart boundary is lost.
    expect(init.headers).toBeUndefined();
    expect(assets).toHaveLength(1);
  });

  test("uploadRecipeAssets returns [] when the envelope carries no assets", async () => {
    mockFetch(okEnvelope(null));
    expect(await uploadRecipeAssets("Ribs.recipe", [], "")).toEqual([]);
  });

  test("setRecipeAssets omits index for splash and includes it otherwise", async () => {
    mockFetch(okEnvelope({ assets: [] }));
    await setRecipeAssets("Ribs.recipe", "splash", ["a1"], undefined, "");
    let body = JSON.parse(String((calls()[0] as [string, RequestInit])[1].body));
    expect(body).toEqual({ file: "Ribs.recipe", section: "splash", assets: ["a1"] });
    expect("index" in body).toBe(false);

    rs.resetAllMocks();
    mockFetch(okEnvelope({ assets: [] }));
    await setRecipeAssets("Ribs.recipe", "ingredients", ["a1"], 3, "");
    body = JSON.parse(String((calls()[0] as [string, RequestInit])[1].body));
    expect(body.index).toBe(3);
  });

  test("setRecipeAssets keeps index 0, which is a real position", async () => {
    mockFetch(okEnvelope({ assets: [] }));
    await setRecipeAssets("Ribs.recipe", "instructions", [], 0, "");

    const body = JSON.parse(String((calls()[0] as [string, RequestInit])[1].body));
    expect(body.index).toBe(0);
  });

  test("deleteRecipeAssets sends the asset list to the delete endpoint", async () => {
    mockFetch(okEnvelope());
    await deleteRecipeAssets("Ribs.recipe", ["a1", "a2"], "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/recipes/assets/delete");
    expect(JSON.parse(String(init.body))).toEqual({
      file: "Ribs.recipe",
      assets: ["a1", "a2"],
    });
  });
});
