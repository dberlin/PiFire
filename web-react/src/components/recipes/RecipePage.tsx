import { useParams } from "react-router";

// Placeholder for the recipe detail route. Task 6 replaces this with the real
// view (metadata, ingredients, steps, assets, run/edit/delete actions) built
// on fetchRecipeDetail from helpers/files/recipeApi. This exists only so
// RecipeList's "New Recipe" flow and the /recipes/:filename route have
// somewhere to land.
export function RecipePage() {
  const { filename } = useParams<{ filename: string }>();
  return <p className="pf-settings-hint">{filename}</p>;
}
