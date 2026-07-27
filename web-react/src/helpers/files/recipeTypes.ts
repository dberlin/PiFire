/** metadata.json. `units` is the recipe's OWN units, which may differ from the
 *  live setting -- the control process converts on the way in. */
export interface RecipeMetadata {
  author: string;
  username: string;
  id: string;
  title: string;
  description: string;
  image: string;
  thumbnail: string;
  units: string;
  prep_time: number;
  cook_time: number;
  rating: number;
  difficulty: string;
  version: string;
  food_probes: number;
}

export interface Ingredient {
  name: string;
  quantity: string;
  assets: string[];
}

/** `ingredients` holds ingredient NAMES, not indices, which is why renaming an
 *  ingredient rewrites every instruction that referenced it. */
export interface Instruction {
  text: string;
  ingredients: string[];
  assets: string[];
  step: number;
}

/** `timer` is MINUTES -- controller.py multiplies by 60. `0` means unset for
 *  hold_temp and for both trigger_temps members. */
export interface RecipeStep {
  mode: string;
  hold_temp: number;
  timer: number;
  notify: boolean;
  message: string;
  pause: boolean;
  trigger_temps: { primary: number; food: number[] };
}

export interface RecipeAsset {
  id: string;
  filename: string;
  type: string;
}

export interface RecipeDetail {
  filename: string;
  metadata: RecipeMetadata;
  recipe: { ingredients: Ingredient[]; instructions: Instruction[]; steps: RecipeStep[] };
  assets: RecipeAsset[];
}
