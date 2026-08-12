export const SETTINGS_TABS = [
  { id: "general", label: "General", editable: true, hideWithoutDcFan: false },
  { id: "work-mode", label: "Work Mode", editable: true, hideWithoutDcFan: false },
  { id: "controller", label: "Controller", editable: true, hideWithoutDcFan: false },
  { id: "pwm", label: "PWM Fan", editable: true, hideWithoutDcFan: true },
  {
    id: "startup",
    label: "Startup / Shutdown",
    editable: true,
    hideWithoutDcFan: false,
  },
  { id: "safety", label: "Safety", editable: true, hideWithoutDcFan: false },
  { id: "pellets", label: "Pellet Levels", editable: true, hideWithoutDcFan: false },
  { id: "history", label: "History", editable: true, hideWithoutDcFan: false },
  {
    id: "notifications",
    label: "Notifications",
    editable: true,
    hideWithoutDcFan: false,
  },
  { id: "units", label: "Units", editable: false, hideWithoutDcFan: false },
  { id: "platform", label: "Platform", editable: false, hideWithoutDcFan: false },
  { id: "probes", label: "Probes", editable: true, hideWithoutDcFan: false },
] as const;

type SettingsTab = (typeof SETTINGS_TABS)[number];
export type SettingsTabId = SettingsTab["id"];
export type EditableSettingsTabId = Extract<SettingsTab, { editable: true }>["id"];
