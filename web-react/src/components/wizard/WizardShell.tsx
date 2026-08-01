import { useState } from "react";
import { useLoaderData, useNavigate } from "react-router";
import { systemAction } from "../../helpers/admin/adminApi";
import { cancelWizard, finishWizard, saveDraft } from "../../helpers/wizard/wizardApi";
import { BASE_URL } from "../../helpers/wizard/wizardRoutes";
import { initialWorking } from "../../helpers/wizard/wizardState";
import type { WizardState, WizardWorking } from "../../helpers/wizard/wizardTypes";
import { InstallProgress } from "./InstallProgress";
import { DisplayStep } from "./steps/DisplayStep";
import { DistanceStep } from "./steps/DistanceStep";
import { GrillPlatformStep } from "./steps/GrillPlatformStep";
import { ProbesStep } from "./steps/ProbesStep";
import "./wizard.css";

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
  detail?: string;
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
      // The backend's detail is already a full sentence and names the device;
      // the fallback only has to cover a 422 that arrives without one.
      return (
        result.detail ??
        "The selected I2C bus configuration conflicts — resolve it before finishing."
      );
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
  const [exiting, setExiting] = useState(false);
  const [exitError, setExitError] = useState<string | null>(null);
  const navigate = useNavigate();

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

  async function handleExit() {
    setExiting(true);
    // Save first, cancel second. The welcome step promises the progress is
    // kept, and the draft has to be persisted before the flag that brought the
    // user here is cleared. Best-effort, like goToStep's flush: a failed save
    // must not trap the user in a wizard they asked to leave.
    try {
      await saveDraft(BASE_URL, working);
    } catch (err) {
      console.warn("Wizard: failed to save draft on exit", err);
    }
    const ok = await cancelWizard(BASE_URL);
    setExiting(false);
    if (!ok) {
      // globals.first_time_setup is still set, so DashboardRoute would bounce
      // us right back here -- staying put with a visible error is the only
      // honest outcome.
      setExitError("Couldn't leave setup — please try again.");
      return;
    }
    setExitError(null);
    // /api/wizard/cancel has cleared globals.first_time_setup, so
    // DashboardRoute's post-mount check (DashboardRoute.tsx:26-38) will NOT
    // send us straight back. Navigating without that clear would loop.
    navigate("/");
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
          onDone={async () => {
            // The install wrote new module selections; control and the webapp
            // have to come back up before they take effect. /admin/restart was
            // a Flask page route and 404s now -- this is the API that replaced
            // it. Land on the dashboard either way: on success the server is
            // going down mid-request, so there is nothing to wait for.
            await systemAction("restart");
            window.location.href = "/";
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
              probes, display, and distance/hopper sensing. Use <strong>Exit Setup</strong> to leave
              at any point; your progress is saved as a draft and picked up next time.
            </p>
          </div>
        );
      case "grillplatform":
        return (
          <GrillPlatformStep
            state={state}
            working={working}
            onChange={setWorking}
            baseUrl={BASE_URL}
          />
        );
      case "distance":
        return (
          <DistanceStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
      case "probes":
        return (
          <ProbesStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
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
        {/* In the chrome rather than the footer so the way out is reachable
            from every step. Suppressed once the install is running (the same
            signal that hides Back/Next): the installer runs detached in its
            own process, so there would be nothing to cancel -- clearing
            first_time_setup mid-install would just drop the user on a
            dashboard whose hardware config is still being rewritten. */}
        {!hideFooter && (
          <button
            type="button"
            className="pf-btn pf-wizard-exit"
            disabled={exiting}
            onClick={() => void handleExit()}
          >
            {exiting ? "Leaving…" : "Exit Setup"}
          </button>
        )}
      </header>
      {exitError && <p className="pf-wizard-finish-error">{exitError}</p>}
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
