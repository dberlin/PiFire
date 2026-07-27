import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { FileRequestError } from "./apiEnvelope";
import {
  createRecipe,
  deleteRecipe,
  fetchRecipeDetail,
  recipeDownloadUrl,
  runRecipe,
  uploadRecipe,
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
