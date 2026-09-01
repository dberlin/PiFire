import type { RecipeDetail, RecipeStep } from "@pifire/core/contracts/content";
import { Fragment, useEffect, useRef } from "react";
import { FALLBACK_THUMB } from "../../helpers/files/fileTypes";
import { assetUrl } from "../../helpers/files/recipeApi";

interface Props {
  detail: RecipeDetail;
  /** The step index the running recipe is on, or null when this recipe is not
   *  the one running. Comes from RecipeRunStatus's derived view, not fetched
   *  here -- see helpers/recipes/runStatus.ts. */
  activeStep: number | null;
}

// `0` is the disabled sentinel for hold_temp and every trigger_temps member
// (controller.py / contracts/content.gen.ts) -- never render it as the number "0".
function formatTrigger(value: number, units: string): string {
  return value === 0 ? "—" : `${value}°${units}`;
}

const KNOWN_DIFFICULTY: Record<string, string> = {
  Easy: "pf-badge-ok",
  Intermediate: "",
  Hard: "pf-badge-warn",
  Advanced: "pf-badge-danger",
};

function DifficultyBadge({ difficulty }: { difficulty: string }) {
  const tone = KNOWN_DIFFICULTY[difficulty] ?? "pf-badge-unknown";
  return <span className={`pf-badge ${tone}`.trim()}>{difficulty}</span>;
}

function Stars({ rating }: { rating: number }) {
  return (
    <span className="pf-rcp-stars" role="img" aria-label={`Rating: ${rating} out of 5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={n <= rating ? "pf-rcp-star pf-rcp-star--filled" : "pf-rcp-star"}>
          {"★"}
        </span>
      ))}
    </span>
  );
}

function StepCard({
  step,
  index,
  active,
  units,
  registerRef,
}: {
  step: RecipeStep;
  index: number;
  active: boolean;
  units: string;
  registerRef: (el: HTMLDivElement | null) => void;
}) {
  const isTimed = step.mode === "Hold" || step.mode === "Smoke";
  return (
    <div ref={registerRef} className={`pf-rcp-step${active ? " pf-rcp-step--active" : ""}`}>
      <div className="pf-rcp-step-number">
        {`Step ${index}`}
        {active && <span className="pf-rcp-step-active-tag"> -- running</span>}
      </div>

      {step.mode === "Startup" && (
        <p className="pf-settings-hint">Transitions once startup completes successfully.</p>
      )}

      {isTimed && (
        <dl className="pf-rcp-dl">
          <dt>Mode</dt>
          <dd>{step.mode}</dd>
          {step.mode === "Hold" && (
            <>
              <dt>Hold temp</dt>
              <dd>{formatTrigger(step.hold_temp, units)}</dd>
            </>
          )}
          <dt>Primary trigger</dt>
          <dd>{formatTrigger(step.trigger_temps.primary, units)}</dd>
          {step.trigger_temps.food.map((t, i) => (
            <Fragment key={i}>
              <dt>{`Food probe ${i + 1} trigger`}</dt>
              <dd>{formatTrigger(t, units)}</dd>
            </Fragment>
          ))}
          {/* Minutes, not a bare count (controller.py multiplies by 60). */}
          <dt>Timer</dt>
          <dd>{`${step.timer} min`}</dd>
          <dt>Pause for input</dt>
          <dd>{step.pause ? "Yes" : "No"}</dd>
          {step.notify && (
            <>
              <dt>Notification</dt>
              <dd>{step.message}</dd>
            </>
          )}
        </dl>
      )}

      {!isTimed && step.mode !== "Startup" && (
        <dl className="pf-rcp-dl">
          <dt>Mode</dt>
          <dd>{step.mode}</dd>
        </dl>
      )}
    </div>
  );
}

// The read-only recipe detail surface (blueprints/recipes/templates/recipes/
// _recipe_view.html): splash, about card, description, ingredients, instructions
// and the program-step list. Read-only: the editors live in their own components.
export function RecipeView({ detail, activeStep }: Props) {
  const { metadata, recipe } = detail;
  const units = metadata.units;
  const activeRef = useRef<HTMLDivElement | null>(null);

  // activeStep drives WHICH element registerRef populated activeRef with, not
  // anything read directly in this body -- it has to stay a dependency so the
  // scroll runs again each time the running step advances.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeStep]);

  return (
    <>
      <div className="pf-section">
        <h2 className="pf-section-title">About This Recipe</h2>
        <div className="pf-section-body pf-rcp-about">
          <img
            className="pf-rcp-splash"
            src={metadata.image ? assetUrl(metadata.id, metadata.image) : FALLBACK_THUMB}
            alt="Recipe"
          />
          <dl className="pf-rcp-dl">
            <dt>Author</dt>
            <dd>{metadata.author}</dd>
            <dt>Rating</dt>
            <dd>
              <Stars rating={metadata.rating} />
            </dd>
            <dt>Prep time</dt>
            <dd>{`${metadata.prep_time} min`}</dd>
            <dt>Cook time</dt>
            <dd>{`${metadata.cook_time} min`}</dd>
            <dt>Difficulty</dt>
            <dd>
              <DifficultyBadge difficulty={metadata.difficulty} />
            </dd>
            <dt>Food probes</dt>
            <dd>{metadata.food_probes}</dd>
          </dl>
        </div>
      </div>

      <div className="pf-section">
        <h2 className="pf-section-title">Description</h2>
        <div className="pf-section-body">
          <p>{metadata.description}</p>
        </div>
      </div>

      <div className="pf-section">
        <h2 className="pf-section-title">Ingredients</h2>
        <div className="pf-section-body">
          {recipe.ingredients.length > 0 ? (
            <table className="pf-rcp-table">
              <thead>
                <tr>
                  <th>Quantity</th>
                  <th>Ingredient</th>
                  <th> </th>
                </tr>
              </thead>
              <tbody>
                {recipe.ingredients.map((ingredient, i) => (
                  <tr key={`${ingredient.name}-${i}`}>
                    <td>{ingredient.quantity}</td>
                    <td>{ingredient.name}</td>
                    <td>
                      {ingredient.assets.map((asset) => (
                        <img
                          key={asset}
                          className="pf-rcp-asset-thumb"
                          src={assetUrl(metadata.id, asset)}
                          alt="thumbnail"
                        />
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="pf-settings-hint">No ingredients listed.</p>
          )}
        </div>
      </div>

      <div className="pf-section">
        <h2 className="pf-section-title">Instructions</h2>
        <div className="pf-section-body">
          {recipe.instructions.length > 0 ? (
            <table className="pf-rcp-table">
              <thead>
                <tr>
                  <th>Direction</th>
                  <th>Ingredients used</th>
                  <th>Program step</th>
                </tr>
              </thead>
              <tbody>
                {recipe.instructions.map((instruction, i) => (
                  <tr key={i}>
                    <td>
                      {instruction.text}
                      {instruction.assets.map((asset) => (
                        <img
                          key={asset}
                          className="pf-rcp-asset-thumb"
                          src={assetUrl(metadata.id, asset)}
                          alt="thumbnail"
                        />
                      ))}
                    </td>
                    <td>
                      <ul className="pf-rcp-ingredients-used">
                        {/* Ingredient NAME strings, not indices -- renaming an
                            ingredient rewrites every instruction that names it. */}
                        {instruction.ingredients.map((name) => (
                          <li key={name}>{name}</li>
                        ))}
                      </ul>
                    </td>
                    <td>{instruction.step === 0 ? "Prep" : `Step ${instruction.step}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="pf-settings-hint">No instructions listed.</p>
          )}
        </div>
      </div>

      <div className="pf-section">
        <h2 className="pf-section-title">Program Steps</h2>
        <div className="pf-section-body">
          {recipe.steps.map((step, i) => (
            <StepCard
              key={i}
              step={step}
              index={i}
              units={units}
              active={activeStep === i}
              registerRef={(el) => {
                if (activeStep === i) activeRef.current = el;
              }}
            />
          ))}
        </div>
      </div>
    </>
  );
}
