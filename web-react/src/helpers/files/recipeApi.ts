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

import { postForm, read, write } from "./apiEnvelope";
import type { RecipeDetail, RecipeMetadata } from "./recipeTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export const fetchRecipeDetail = (file: string, baseUrl = BASE_URL) =>
  read<RecipeDetail>("recipes", "detail", file, baseUrl);

/** A whole-metadata patch, same shape recipes_api.py's set_metadata() accepts
 * -- an unknown key is refused with 400 `data.field`, so this is typed as a
 * subset of RecipeMetadata rather than a bare Record. */
export type RecipeMetadataFields = Partial<
  Pick<
    RecipeMetadata,
    | "title"
    | "author"
    | "description"
    | "difficulty"
    | "units"
    | "prep_time"
    | "cook_time"
    | "rating"
    | "food_probes"
  >
>;

export const saveRecipeMetadata = (
  file: string,
  fields: RecipeMetadataFields,
  baseUrl = BASE_URL,
) => write<null>("recipes", "metadata", { file, fields }, baseUrl);

/** Appends a blank ingredient row (recipes_api.py's add_ingredient) -- there
 * is nothing to name it yet, so the caller refetches and edits the new row
 * in place via updateIngredient. */
export const addIngredient = (file: string, baseUrl = BASE_URL) =>
  write<null>("recipes", "ingredients", { file, action: "add" }, baseUrl);

/** Renames/requantifies ingredient `index`. A rename cascades server-side
 * into every instruction that names the OLD value -- refetch the whole
 * detail afterwards rather than patching local state. */
export const updateIngredient = (
  file: string,
  index: number,
  name: string,
  quantity: string,
  baseUrl = BASE_URL,
) =>
  write<null>("recipes", "ingredients", { file, action: "update", index, name, quantity }, baseUrl);

/** Deletes ingredient `index`. The server also strips this ingredient's name
 * out of every instruction that referenced it, so a refetch is required to
 * see the true post-delete instruction list. */
export const deleteIngredient = (file: string, index: number, baseUrl = BASE_URL) =>
  write<null>("recipes", "ingredients", { file, action: "delete", index }, baseUrl);

/** Appends a blank instruction row (recipes_api.py's add_instruction). */
export const addInstruction = (file: string, baseUrl = BASE_URL) =>
  write<null>("recipes", "instructions", { file, action: "add" }, baseUrl);

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
) =>
  write<null>(
    "recipes",
    "instructions",
    { file, action: "update", index, text, ingredients, step },
    baseUrl,
  );

export const deleteInstruction = (file: string, index: number, baseUrl = BASE_URL) =>
  write<null>("recipes", "instructions", { file, action: "delete", index }, baseUrl);

export const createRecipe = (baseUrl = BASE_URL) =>
  write<{ filename: string }>("recipes", "create", {}, baseUrl);

export const deleteRecipe = (file: string, baseUrl = BASE_URL) =>
  write<null>("recipes", "delete", { file }, baseUrl);

/** Starts a real cook. The endpoint answers 409 `not_stopped` unless the grill
 * is stopped, which surfaces here as a FileRequestError carrying that status. */
export const runRecipe = (file: string, baseUrl = BASE_URL) =>
  write<{ filename: string }>("recipes", "run", { file }, baseUrl);

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
