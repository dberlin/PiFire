// Derives what the recipe detail page needs to know about a run in progress
// from `socket_dash_data`'s `recipeStatus` -- the same field the dashboard
// already keys buttonsForMode/countdowns/deriveView off (LiveState.recipeStatus,
// helpers/types.ts). There is no separate fetch or poll here: Flask's
// /reciperunstatus (recipes.js:289) exists only because the recipe page has
// no socket; this app already has one live in AppShell.
//
// recipeStatus.filename is the BARE running filename (blueprints/mobile/
// socket_io.py:330-336 strips the path), which is exactly what useParams gives
// this page, so a plain string compare is enough -- no path normalisation.
import type { LiveState } from "../types";

export interface RecipeRunView {
  /** True only when a recipe IS running and it is THIS file. */
  active: boolean;
  /** Set when a DIFFERENT recipe is the one running. Flask's control loop
   *  ignores the requested filename entirely and just drives whichever recipe
   *  is loaded (G9); a client-side view has to say so rather than silently
   *  highlighting a step that belongs to the wrong recipe. */
  otherFilename: string | null;
  paused: boolean;
  /** Only meaningful when `active` is true. */
  step: number;
  /** The Run button may only start a cook from a fully stopped grill. */
  canRun: boolean;
}

export function deriveRunView(status: LiveState["recipeStatus"], filename: string): RecipeRunView {
  const active = status.recipeMode && status.filename === filename;
  return {
    active,
    otherFilename: status.recipeMode && !active ? status.filename : null,
    paused: status.paused,
    step: status.step,
    canRun: status.mode === "Stop",
  };
}
