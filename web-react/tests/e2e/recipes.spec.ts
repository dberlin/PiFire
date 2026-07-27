import { expect, test } from "@playwright/test";
import { API, ensureStopped } from "./helpers";

// Round trip for the recipe editor against a real backend.
//
// SAFETY: this spec must never start a cook. runRecipe POSTs
// /api/files/recipes/run, which moves the grill into Recipe mode -- the
// suite runs workers: 1 against ONE shared PiFire (playwright.config.ts), and
// entering Recipe mode both steals the global grill mode from whichever spec
// runs next and, like Startup in roundtrip.spec.ts, flushes the ENTIRE
// history store out from under history.spec.ts. So this spec creates its own
// recipe through the UI, edits it, and deletes it again -- never clicking
// "Run this recipe". The one thing it asserts about the Run button is that it
// reflects live state (enabled once the grill is confirmed Stopped), not that
// clicking it does anything.

async function removeRecipe(request: import("@playwright/test").APIRequestContext, file: string) {
  await request.post(`${API}/api/files/recipes/delete`, { data: { file } });
}

test.describe("recipe editor", () => {
  test("creates, edits, inserts a step, and deletes a recipe -- never running it", async ({
    page,
    request,
  }) => {
    await ensureStopped(request);

    await page.goto("/recipes");
    await page.getByRole("button", { name: "New recipe" }).click();
    await expect(page).toHaveURL(/\/recipes\/[^/]+$/);
    const filename = decodeURIComponent(new URL(page.url()).pathname.split("/").pop() ?? "");
    expect(filename, "New recipe did not navigate to a filename").toMatch(/\.pfrecipe$/);

    try {
      // The grill is confirmed Stopped, so Run reflects that -- asserted,
      // never clicked. This is the only claim this spec makes about Run.
      const runButton = page.getByRole("button", { name: "Run this recipe" });
      await expect(runButton).toBeVisible();
      await expect(runButton).toBeEnabled();

      // The seeded defaults (file_mgmt/recipes.py's _default_recipe_steps)
      // include Startup/Shutdown steps this editor cannot construct itself --
      // StepsEditor Step 2 exists so they still render, read-only.
      await expect(page.getByText("Step 0 -- Startup")).toBeVisible();
      await expect(page.getByText("Step 2 -- Shutdown")).toBeVisible();

      // Edit metadata.
      await page.getByLabel("Title", { exact: true }).fill("E2E Recipe Roundtrip");
      await page.getByLabel("Author", { exact: true }).fill("E2E Tester");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(page.getByRole("heading", { name: "E2E Recipe Roundtrip" })).toBeVisible();

      // Add an ingredient.
      await page.getByRole("button", { name: "Add ingredient" }).click();
      await expect(page.getByLabel("Name for ingredient 1")).toBeVisible();
      await page.getByLabel("Name for ingredient 1").fill("Brisket");
      await page.getByLabel("Quantity for ingredient 1").fill("1 whole");
      await page.getByRole("button", { name: "Save ingredient 1" }).click();
      await expect(page.getByRole("button", { name: "Save ingredient 1" })).toBeDisabled();

      // Add an instruction referencing it -- recipes_api.py's
      // update_instruction refuses any ingredient name not currently in the
      // recipe, so this proves the two editors' server-side ordering.
      await page.getByRole("button", { name: "Add instruction" }).click();
      //  exact: true, because getByLabel substring-matches and the step select
      //  beside this field is labelled "Program step for direction 1".
      const direction = page.getByLabel("Direction 1", { exact: true });
      await expect(direction).toBeVisible();
      await direction.fill("Trim the fat cap.");
      await page.getByLabel("Brisket", { exact: true }).check();
      await page.getByRole("button", { name: "Save direction 1" }).click();
      await expect(page.getByRole("button", { name: "Save direction 1" })).toBeDisabled();

      // Insert a step -- POSITIONAL, above the seeded Shutdown step, not a
      // trailing append.
      await page.getByRole("button", { name: "Insert a step above Step 2" }).click();
      await expect(page.getByText("Step 3 -- Shutdown")).toBeVisible();

      // Everything survives a reload, proving it actually round-tripped
      // through the server rather than staying local component state.
      await page.reload();
      await expect(page.getByRole("heading", { name: "E2E Recipe Roundtrip" })).toBeVisible();
      await expect(page.getByText("Brisket").first()).toBeVisible();
      //  The cell, not getByText: the editor's textarea below holds the same
      //  string, and asserting on the read-only table is the actual intent --
      //  that the write round-tripped and the view refetched it.
      await expect(page.getByRole("cell", { name: "Trim the fat cap." })).toBeVisible();
      await expect(page.getByText("Step 3 -- Shutdown")).toBeVisible();

      // Delete, through the UI, as the round trip's own last step.
      await page.goto("/recipes");
      const row = page.getByRole("link", { name: "E2E Recipe Roundtrip", exact: true });
      await expect(row).toBeVisible();
      await page.getByRole("button", { name: `Delete ${filename}`, exact: true }).click();
      await page.getByRole("button", { name: "Confirm", exact: true }).click();
      await expect(row).toHaveCount(0);
    } finally {
      // Defensive: harmless if the UI delete above already landed -- a
      // broken run must not leave the fixture behind for the next spec's
      // /recipes listing.
      await removeRecipe(request, filename);
    }
  });
});
