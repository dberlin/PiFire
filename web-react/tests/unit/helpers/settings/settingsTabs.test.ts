import { describe, expect, it } from "@rstest/core";

import type {
  EditableSettingsTabId,
  SettingsTabId,
} from "../../../../src/helpers/settings/settingsTabs";
import { SETTINGS_TABS } from "../../../../src/helpers/settings/settingsTabs";

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;

type ExpectedSettingsTabId =
  | "general"
  | "work-mode"
  | "controller"
  | "pwm"
  | "startup"
  | "safety"
  | "pellets"
  | "history"
  | "notifications"
  | "units"
  | "platform"
  | "probes";

type ExpectedEditableSettingsTabId = Exclude<ExpectedSettingsTabId, "units" | "platform">;

const settingsTabIdsAreExact: Equal<SettingsTabId, ExpectedSettingsTabId> = true;
const editableSettingsTabIdsAreExact: Equal<EditableSettingsTabId, ExpectedEditableSettingsTabId> =
  true;

const EXPECTED_TABS = [
  { id: "general", label: "General" },
  { id: "work-mode", label: "Work Mode" },
  { id: "controller", label: "Controller" },
  { id: "pwm", label: "PWM Fan" },
  { id: "startup", label: "Startup / Shutdown" },
  { id: "safety", label: "Safety" },
  { id: "pellets", label: "Pellet Levels" },
  { id: "history", label: "History" },
  { id: "notifications", label: "Notifications" },
  { id: "units", label: "Units" },
  { id: "platform", label: "Platform" },
  { id: "probes", label: "Probes" },
] as const;

const EXPECTED_EDITABLE_IDS = [
  "general",
  "work-mode",
  "controller",
  "pwm",
  "startup",
  "safety",
  "pellets",
  "history",
  "notifications",
  "probes",
] as const;

describe("SETTINGS_TABS", () => {
  it("preserves the settings navigation IDs, labels, and order", () => {
    expect(SETTINGS_TABS.map(({ id, label }) => ({ id, label }))).toEqual(EXPECTED_TABS);
    expect(settingsTabIdsAreExact).toBe(true);
  });

  it("assigns every tab a unique route ID", () => {
    const ids = SETTINGS_TABS.map(({ id }) => id);

    expect(new Set(ids).size).toBe(ids.length);
  });

  it("hides only PWM when the grill has no DC fan", () => {
    expect(
      SETTINGS_TABS.filter(({ hideWithoutDcFan }) => hideWithoutDcFan).map(({ id }) => id),
    ).toEqual(["pwm"]);
  });

  it("keeps probes last", () => {
    expect(SETTINGS_TABS.at(-1)?.id).toBe("probes");
  });

  it("derives draft-capable identities from editable metadata", () => {
    expect(SETTINGS_TABS.filter(({ editable }) => editable).map(({ id }) => id)).toEqual(
      EXPECTED_EDITABLE_IDS,
    );
    expect(editableSettingsTabIdsAreExact).toBe(true);
  });
});
