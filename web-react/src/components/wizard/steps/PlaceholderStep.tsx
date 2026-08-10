import type { WizardSection } from "../../../helpers/contracts/wizard.gen";

export interface PlaceholderStepProps {
  section: WizardSection;
}

const SECTION_LABELS: Record<WizardSection, string> = {
  grillplatform: "Grill Platform",
  display: "Display",
  distance: "Distance / Hopper",
  probes: "Probes",
};

export function PlaceholderStep({ section }: PlaceholderStepProps) {
  return (
    <div className="pf-wizard-step pf-wizard-step-placeholder" data-step={section}>
      <h2 className="pf-wizard-step-title">{SECTION_LABELS[section] ?? section}</h2>
      <p className="pf-wizard-placeholder-message">
        This section isn't configurable here yet — coming in a later release.
      </p>
    </div>
  );
}
