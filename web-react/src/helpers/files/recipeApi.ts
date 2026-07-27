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
import type { RecipeDetail } from "./recipeTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export const fetchRecipeDetail = (file: string, baseUrl = BASE_URL) =>
  read<RecipeDetail>("recipes", "detail", file, baseUrl);

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
