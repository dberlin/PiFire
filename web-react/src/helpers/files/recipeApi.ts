// Typed client for the /api/files/recipes/* surface.
//
// Every call names a recipe by its BARE FILENAME, resolved the same way as
// cookfileApi.ts: through common/file_browser.py's resolve_managed_file.
// Nothing here ever sends a path.
//
// Write responses use common/app.py's api_response envelope
// {data, result, message} with result === "OK" on success. The detail read
// is a bare payload with an HTTP status -- and unlike CookFileDetail, it has
// no `comments` key.

import type {
  EmptyContentRequest,
  FileAssetsRequest,
  FileRequest,
  RecipeAsset,
  RecipeDetail,
  RecipeIndexedAssetAssignmentRequest,
  RecipeIngredientAddRequest,
  RecipeIngredientDeleteRequest,
  RecipeIngredientUpdateRequest,
  RecipeInstructionAddRequest,
  RecipeInstructionDeleteRequest,
  RecipeInstructionUpdateRequest,
  RecipeMetadataFields,
  RecipeMetadataUpdateRequest,
  RecipeSplashAssetAssignmentRequest,
  RecipeStep,
  RecipeStepDeleteRequest,
  RecipeStepInsertRequest,
  RecipeStepUpdateRequest,
} from "@pifire/core/contracts/content";
import { postForm, read, write } from "./apiEnvelope";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export const fetchRecipeDetail = (file: string, baseUrl = BASE_URL) =>
  read<RecipeDetail>("recipes", "detail", file, baseUrl);

export const saveRecipeMetadata = (
  file: string,
  fields: RecipeMetadataFields,
  baseUrl = BASE_URL,
) => {
  const body: RecipeMetadataUpdateRequest = { file, fields };
  return write<null>("recipes", "metadata", body, baseUrl);
};

/** Appends a blank ingredient row (recipes_api.py's add_ingredient) -- there
 * is nothing to name it yet, so the caller refetches and edits the new row
 * in place via updateIngredient. */
export const addIngredient = (file: string, baseUrl = BASE_URL) => {
  const body: RecipeIngredientAddRequest = { file, action: "add" };
  return write<null>("recipes", "ingredients", body, baseUrl);
};

/** Renames/requantifies ingredient `index`. A rename cascades server-side
 * into every instruction that names the OLD value -- refetch the whole
 * detail afterwards rather than patching local state. */
export const updateIngredient = (
  file: string,
  index: number,
  name: string,
  quantity: string,
  baseUrl = BASE_URL,
) => {
  const body: RecipeIngredientUpdateRequest = { file, action: "update", index, name, quantity };
  return write<null>("recipes", "ingredients", body, baseUrl);
};

/** Deletes ingredient `index`. The server also strips this ingredient's name
 * out of every instruction that referenced it, so a refetch is required to
 * see the true post-delete instruction list. */
export const deleteIngredient = (file: string, index: number, baseUrl = BASE_URL) => {
  const body: RecipeIngredientDeleteRequest = { file, action: "delete", index };
  return write<null>("recipes", "ingredients", body, baseUrl);
};

/** Appends a blank instruction row (recipes_api.py's add_instruction). */
export const addInstruction = (file: string, baseUrl = BASE_URL) => {
  const body: RecipeInstructionAddRequest = { file, action: "add" };
  return write<null>("recipes", "instructions", body, baseUrl);
};

/** Replaces instruction `index` wholesale: text, the ingredient NAME list,
 * and its program step. `ingredients` must be names present in this
 * recipe's ingredient list right now -- an unknown name is refused with 400
 * `data.field == "ingredients"`, which is why the picker is a multi-select
 * over the live list rather than free text. */
export const updateInstruction = (
  file: string,
  index: number,
  text: string,
  ingredients: string[],
  step: number,
  baseUrl = BASE_URL,
) => {
  const body: RecipeInstructionUpdateRequest = {
    file,
    action: "update",
    index,
    text,
    ingredients,
    step,
  };
  return write<null>("recipes", "instructions", body, baseUrl);
};

export const deleteInstruction = (file: string, index: number, baseUrl = BASE_URL) => {
  const body: RecipeInstructionDeleteRequest = { file, action: "delete", index };
  return write<null>("recipes", "instructions", body, baseUrl);
};

/** Inserts a new default step at `index` -- POSITIONAL (recipes_api.py's
 * insert_step, matching Flask's own stepAdd), not appended: `index ==
 * steps.length` is legal and appends at the end, anything else shifts every
 * later step down one. There is no separate append endpoint, which is why
 * the editor always calls this with an explicit index rather than offering a
 * single trailing "Add step" button. */
export const insertStep = (file: string, index: number, baseUrl = BASE_URL) => {
  const body: RecipeStepInsertRequest = { file, action: "insert", index };
  return write<null>("recipes", "steps", body, baseUrl);
};

/** Replaces step `index` wholesale. `0` is the disabled sentinel for
 * hold_temp and every trigger_temps member -- a legal value, not a missing
 * one -- so every field here must be a real number/boolean, never omitted.
 * trigger_temps.food must carry exactly one entry per the recipe's current
 * food_probes count; a mismatch is refused with 400 `data.field ==
 * "trigger_temps"`. */
export const updateStep = (file: string, index: number, step: RecipeStep, baseUrl = BASE_URL) => {
  const body: RecipeStepUpdateRequest = { file, action: "update", index, step };
  return write<null>("recipes", "steps", body, baseUrl);
};

export const deleteStep = (file: string, index: number, baseUrl = BASE_URL) => {
  const body: RecipeStepDeleteRequest = { file, action: "delete", index };
  return write<null>("recipes", "steps", body, baseUrl);
};

/** Multipart: adds one or more images to the recipe's asset pool WITHOUT
 * attaching them to any section -- attaching is a separate whole-list write
 * via setRecipeAssets. Field name is `assets` (recipes_api.py's upload_assets
 * reads request.files.getlist("assets")); `file` rides alongside it as a
 * form field. */
export async function uploadRecipeAssets(
  file: string,
  images: File[],
  baseUrl = BASE_URL,
): Promise<RecipeAsset[]> {
  const form = new FormData();
  form.append("file", file);
  for (const image of images) form.append("assets", image);
  const data = await postForm<{ assets: RecipeAsset[] }>("recipes", "assets/upload", form, baseUrl);
  return data?.assets ?? [];
}

/** Replaces one section's asset list wholesale -- the client sends the
 * complete list a section should end up with, rather than a single
 * add/remove action Flask infers direction for. `index` is required for
 * `ingredients`/`instructions` and must be omitted for `splash`. */
export function setRecipeAssets(
  file: string,
  section: RecipeSplashAssetAssignmentRequest["section"],
  assets: string[],
  baseUrl?: string,
): Promise<{ assets: string[] }>;
export function setRecipeAssets(
  file: string,
  section: RecipeIndexedAssetAssignmentRequest["section"],
  assets: string[],
  index: number,
  baseUrl?: string,
): Promise<{ assets: string[] }>;
export function setRecipeAssets(
  file: string,
  section:
    | RecipeSplashAssetAssignmentRequest["section"]
    | RecipeIndexedAssetAssignmentRequest["section"],
  assets: string[],
  indexOrBaseUrl?: number | string,
  baseUrl = BASE_URL,
) {
  if (section === "splash") {
    const body: RecipeSplashAssetAssignmentRequest = { file, section, assets };
    const splashBaseUrl = typeof indexOrBaseUrl === "string" ? indexOrBaseUrl : baseUrl;
    return write<{ assets: string[] }>("recipes", "assets", body, splashBaseUrl);
  }
  if (typeof indexOrBaseUrl !== "number") {
    throw new TypeError("Recipe ingredient and instruction assets require an index.");
  }
  const body: RecipeIndexedAssetAssignmentRequest = {
    file,
    section,
    index: indexOrBaseUrl,
    assets,
  };
  return write<{ assets: string[] }>("recipes", "assets", body, baseUrl);
}

/** Deletes assets from the recipe archive outright. remove_assets already
 * scrubs metadata.image/thumbnail and every ingredient's/instruction's own
 * asset list server-side, so a refetch is all that is needed afterwards. */
export const deleteRecipeAssets = (file: string, assets: string[], baseUrl = BASE_URL) =>
  write<null>("recipes", "assets/delete", { file, assets } satisfies FileAssetsRequest, baseUrl);

export const createRecipe = (baseUrl = BASE_URL) =>
  write<{ filename: string }>("recipes", "create", {} satisfies EmptyContentRequest, baseUrl);

export const deleteRecipe = (file: string, baseUrl = BASE_URL) =>
  write<null>("recipes", "delete", { file } satisfies FileRequest, baseUrl);

/** Starts a real cook. The endpoint answers 409 `not_stopped` unless the grill
 * is stopped, which surfaces here as a FileRequestError carrying that status. */
export const runRecipe = (file: string, baseUrl = BASE_URL) =>
  write<{ filename: string }>("recipes", "run", { file } satisfies FileRequest, baseUrl);

/** Multipart: the archive rides under the field name `recipe`, unlike
 * cookfileApi's `uploadCookFile`, which uses `file`. */
export async function uploadRecipe(archive: File, baseUrl = BASE_URL): Promise<string> {
  const form = new FormData();
  form.append("recipe", archive);
  const data = await postForm<{ filename: string }>("recipes", "upload", form, baseUrl);
  return data?.filename ?? archive.name;
}

/** A plain URL, not a fetch: the download is an <a href download>, which is why
 * the endpoint is GET where Flask's is POST. */
export const recipeDownloadUrl = (file: string, baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/recipes/download?file=${encodeURIComponent(file)}`;

/** Splash image and every ingredient/instruction asset filename resolve the
 * same way: static/img/tmp/{recipe id}/{filename} (file_mgmt/common.py), with
 * no thumbs/ subfolder the way cook-file assets have -- _recipe_view.html
 * always points straight at the full-size file. */
export const assetUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/${name}`;
