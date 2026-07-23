import { useState } from "react";
import { useLoaderData } from "react-router";
import { finishWizard, saveDraft } from "../../helpers/wizard/wizardApi";
import { BASE_URL } from "../../helpers/wizard/wizardRoutes";
import { initialWorking } from "../../helpers/wizard/wizardState";
import type { WizardState, WizardWorking } from "../../helpers/wizard/wizardTypes";
import { InstallProgress } from "./InstallProgress";
import { DisplayStep } from "./steps/DisplayStep";
import { PlaceholderStep } from "./steps/PlaceholderStep";

export const STEPS = [
  "welcome",
  "grillplatform",
  "probes",
  "display",
  "distance",
  "finish",
] as const;
type Step = (typeof STEPS)[number];

interface FinishResult {
  ok: boolean;
  status: number;
  message?: string;
}

const STEP_LABELS: Record<Step, string> = {
  welcome: "Welcome",
  grillplatform: "Grill Platform",
  probes: "Probes",
  display: "Display",
  distance: "Distance / Hopper",
  finish: "Finish",
};

function finishErrorMessage(result: FinishResult): string {
  switch (result.status) {
    case 400:
      return "Please choose a module for every hardware section before finishing.";
    case 422:
      return "The selected I2C bus configuration conflicts — resolve it before finishing.";
    default:
      return result.message ?? "Something went wrong finishing setup.";
  }
}

// Rendered while the /wizard route's loader runs on initial hydration —
// keeps react-router from warning "No HydrateFallback element provided".
export function HydrateFallback() {
  return <div className="pf-fit" />;
}

// Route errorElement for /wizard -- rendered if wizardLoader's fetch fails.
export function WizardError() {
  return <div className="pf-fit pf-wizard-error">Couldn't load the setup wizard.</div>;
}

export function WizardShell() {
  const state = useLoaderData() as WizardState;
  const [working, setWorking] = useState<WizardWorking>(() => initialWorking(state));
  const [step, setStep] = useState(0);
  const [finishState, setFinishState] = useState<FinishResult | null>(null);
  const [finishing, setFinishing] = useState(false);

  const currentStep = STEPS[step];
  // Belt-and-suspenders with the server's 409 (system_active): the server is
  // the source of truth, this just avoids a round trip for the common case.
  const canFinish = state.control_mode === "Stop";

  async function goToStep(next: number) {
    // Draft flush is best-effort: navigation must proceed even if the
    // save fails (e.g. a transient network error) -- don't let a rejected
    // saveDraft strand the user on the current step.
    try {
      await saveDraft(BASE_URL, working);
    } catch (err) {
      console.warn("Wizard: failed to save draft", err);
    }
    setStep(next);
  }

  function handleBack() {
    void goToStep(step - 1);
  }

  function handleNext() {
    void goToStep(step + 1);
  }

  async function handleFinish() {
    setFinishing(true);
    const result = await finishWizard(BASE_URL, working);
    setFinishing(false);
    setFinishState(result);
  }

  function renderFinishStep() {
    if (finishState?.ok) {
      return (
        <InstallProgress
          baseUrl={BASE_URL}
          onDone={() => {
            window.location.href = "/admin/restart";
          }}
        />
      );
    }

    const showSystemActiveModal =
      finishState !== null && !finishState.ok && finishState.status === 409;

    return (
      <div className="pf-wizard-step pf-wizard-step-finish" data-step="finish">
        <h2 className="pf-wizard-step-title">Finish</h2>
        <p>Review your selections, then finish setup to install the selected modules.</p>
        {!canFinish && (
          <p className="pf-wizard-finish-note">
            Stop the grill before finishing setup — installation can't run while it's active.
          </p>
        )}
        {finishState && !finishState.ok && finishState.status !== 409 && (
          <p className="pf-wizard-finish-error">{finishErrorMessage(finishState)}</p>
        )}
        <button
          type="button"
          className="pf-btn pf-btn-primary"
          disabled={!canFinish || finishing}
          onClick={() => void handleFinish()}
        >
          Finish
        </button>
        {showSystemActiveModal && (
          <div
            className="pf-wizard-modal pf-wizard-system-active-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Grill is active"
          >
            <p>Can't install while the grill is active — stop it first.</p>
            <button type="button" className="pf-btn" onClick={() => setFinishState(null)}>
              Close
            </button>
          </div>
        )}
      </div>
    );
  }

  function renderStepBody() {
    switch (currentStep) {
      case "welcome":
        return (
          <div className="pf-wizard-step" data-step="welcome">
            <h2 className="pf-wizard-step-title">Welcome</h2>
            <p>
              This wizard walks through configuring PiFire's hardware modules — grill platform,
              probes, display, and distance/hopper sensing. You can leave at any point; your
              progress is saved as a draft.
            </p>
          </div>
        );
      case "grillplatform":
      case "probes":
      case "distance":
        return <PlaceholderStep section={currentStep} />;
      case "display":
        return (
          <DisplayStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
      case "finish":
        return renderFinishStep();
      default:
        return null;
    }
  }

  const hideFooter = currentStep === "finish" && finishState?.ok === true;

  return (
    <div className="pf-wizard">
      <header className="pf-wizard-header">
        <div className="pf-wizard-title">Setup Wizard</div>
        <div className="pf-wizard-steps">
          {STEPS.map((s, i) => (
            <span key={s} className={`pf-wizard-step-indicator ${i === step ? "active" : ""}`}>
              {STEP_LABELS[s]}
            </span>
          ))}
        </div>
      </header>
      <main className="pf-wizard-content">{renderStepBody()}</main>
      {!hideFooter && (
        <footer className="pf-wizard-footer">
          <button type="button" className="pf-btn" disabled={step === 0} onClick={handleBack}>
            Back
          </button>
          {currentStep !== "finish" && (
            <button type="button" className="pf-btn pf-btn-primary" onClick={handleNext}>
              Next
            </button>
          )}
        </footer>
      )}
    </div>
  );
}
