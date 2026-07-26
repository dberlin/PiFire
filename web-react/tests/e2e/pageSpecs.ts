import type { PageSpec, StyleProbe } from "./layoutBaseline";

// The two viewports the fidelity gate is defined at. 1280x720 is the desktop
// regression target the dashboard was authored for; 390x844 is the phone the
// reflow slice introduced. (800x480 -- the grill's own screen -- keeps its own
// dedicated `panel` project; this gate does not duplicate it.)
export const DESKTOP = { width: 1280, height: 720 };
export const PHONE = { width: 390, height: 844 };

// The app shell wraps every page except /wizard, so these ride along on each
// spec below rather than getting a page of their own.
const SHELL = [
  ".pf-shell",
  ".pf-nav",
  ".pf-nav-brand",
  ".pf-nav-mark",
  ".pf-nav-grill",
  ".pf-nav-list",
  ".pf-nav-item",
  ".pf-nav-link",
  ".pf-nav-actions",
  ".pf-nav-timer",
  ".pf-shell-main",
];

// Every structural rule settings.css owns. /history reuses .pf-settings,
// .pf-section and .pf-settings-actions, which is why Task 11 has to re-run the
// history baselines too.
const SETTINGS = [
  ".pf-settings",
  ".pf-settings-nav",
  ".pf-settings-back",
  ".pf-settings-title",
  ".pf-settings-link",
  ".pf-settings-content",
  ".pf-settings-actions",
  ".pf-settings-hint",
  ".pf-section",
  ".pf-section-title",
  ".pf-section-body",
  ".pf-field",
  ".pf-field-label",
  ".pf-field-control",
  ".pf-field-hint",
  ".pf-input",
  ".pf-switch",
];

const WIZARD = [
  ".pf-wizard",
  ".pf-wizard-header",
  ".pf-wizard-title",
  ".pf-wizard-steps",
  ".pf-wizard-step-indicator",
  ".pf-wizard-exit",
  ".pf-wizard-content",
  ".pf-wizard-step",
  ".pf-wizard-step-title",
  ".pf-wizard-footer",
  ".pf-btn",
];

const SETTINGS_TABS: { path: string; label: string }[] = [
  { path: "general", label: "General" },
  { path: "work-mode", label: "Work Mode" },
  { path: "controller", label: "Controller" },
  { path: "pwm", label: "PWM Fan" },
  { path: "startup", label: "Startup / Shutdown" },
  { path: "safety", label: "Safety" },
  { path: "pellets", label: "Pellet Levels" },
  { path: "history", label: "History" },
  { path: "notifications", label: "Notifications" },
  { path: "units", label: "Units" },
  { path: "platform", label: "Platform" },
];

export const PAGE_SPECS: PageSpec[] = [
  // The dashboard, measured by SELECTOR rather than by data-pf. This does not
  // replace dashboard-fidelity.spec.ts -- that one still runs, still
  // scale-normalises, still holds the seven EXACT constants. This one adds the
  // computed-style half, which is the half an @apply rewrite can break.
  {
    name: "dashboard",
    path: "/",
    ready: '[data-pf="stage"]',
    root: ".pf-shell",
    landmarks: [
      ...SHELL,
      ".pf-dash",
      ".pf-dash-header",
      ".pf-dash-brand",
      ".pf-dash-clock",
      ".pf-dash-body",
      ".pf-dash-probecol",
      ".pf-dash-centercol",
      ".pf-dash-rightcol",
      ".pf-dash-card",
      ".pf-dash-gauge",
      ".pf-dash-probecard",
      ".pf-dash-system",
      ".pf-dash-hopper",
      ".pf-dash-cookrow",
      ".pf-dash-pills",
      ".pf-dash-controls",
      ".pf-btn",
    ],
  },
  {
    name: "history",
    path: "/history",
    ready: ".pf-history-chart",
    root: ".pf-shell",
    landmarks: [
      ...SHELL,
      ".pf-settings",
      ".pf-section",
      ".pf-section-title",
      ".pf-section-body",
      ".pf-settings-actions",
      ".pf-history-chart",
    ],
  },
  ...SETTINGS_TABS.map(
    (t): PageSpec => ({
      name: `settings-${t.path}`,
      path: `/settings/${t.path}`,
      ready: ".pf-settings-content .pf-section",
      root: ".pf-shell",
      landmarks: [...SHELL, ...SETTINGS],
    }),
  ),
];

const NEXT = 'button:text-is("Next")';
const WIZARD_STEPS: { name: string; heading: string }[] = [
  { name: "welcome", heading: "Welcome" },
  { name: "grillplatform", heading: "Grill Platform" },
  { name: "probes", heading: "Probes" },
  { name: "display", heading: "Display" },
  { name: "distance", heading: "Distance / Hopper" },
  { name: "finish", heading: "Finish" },
];

PAGE_SPECS.push(
  ...WIZARD_STEPS.map(
    (s, i): PageSpec => ({
      name: `wizard-${s.name}`,
      path: "/wizard",
      // Reached by clicking Next i times from Welcome. Finish is NEVER clicked:
      // it fires the real installer.
      clicks: Array.from({ length: i }, () => NEXT),
      ready: `h2:text-is("${s.heading}")`,
      root: ".pf-wizard",
      landmarks: [
        ...WIZARD,
        ".pf-module-card",
        ".pf-module-details",
        ".pf-module-image",
        ".pf-probes-card",
        ".pf-btn-primary",
      ],
    }),
  ),
);

/** /pellets is the one surface stubApi cannot make deterministic: its data
 *  arrives on the socket_pellet_data channel, not over REST, and the demo
 *  server opens no socket at all -- PelletsPage then renders nothing but its
 *  "Loading pellet database..." branch. So it runs against the APP server and
 *  the live backend, and pellets-fidelity.spec.ts fingerprints the store
 *  before trusting the numbers. See Task 5. */
export const PELLETS_SPEC: PageSpec = {
  name: "pellets",
  path: "/pellets",
  // `section[aria-label=...]`, NOT `[role="region"][aria-label=...]`. VocabTable
  // renders a bare <section aria-label={title}>; role="region" is that element's
  // IMPLICIT role and never appears as an attribute, so an attribute selector
  // for it matches nothing.
  ready: 'section[aria-label="Brands"]',
  root: ".pf-shell",
  landmarks: [
    ...SHELL,
    ".pf-pellets",
    ".pf-pellets-card",
    ".pf-pellets-card-title",
    ".pf-pellets-scroll",
    ".pf-pellets-meter",
    ".pf-pellets-level-text",
    ".pf-pellets-usage",
    ".pf-pellets-actions",
    ".pf-pellets-profile",
  ],
};

/** Chrome that never renders under a fixed fixture, probed synthetically.
 *
 *  Banners returns null with no errors and no warnings; TimerBar renders only
 *  while a timer is visible; the wizard's three role="dialog" surfaces need a
 *  running grill or a finished installer. This proves the rules resolve and to
 *  WHAT -- it proves nothing about how they look in situ, which is the human
 *  checkpoint's job. tests/e2e/wizard-layout.spec.ts already probes two rules
 *  this way; this is the same technique with a committed baseline. */
export const CHROME_PROBES: StyleProbe[] = [
  { name: "banner-error", className: "pf-banner pf-banner--error" },
  { name: "banner-warning", className: "pf-banner pf-banner--warning" },
  { name: "banner-critical", className: "pf-banner pf-banner--critical" },
  { name: "banners", className: "pf-banners" },
  { name: "timer-bar", className: "pf-timer-bar" },
  { name: "timer-readout", className: "pf-timer-readout" },
  { name: "timer-btn", className: "pf-timer-btn" },
  { name: "modal-scrim", className: "pf-modal-scrim-fixed" },
  { name: "wizard-modal", className: "pf-wizard-modal pf-wizard-system-active-modal" },
  { name: "install-bar", className: "pf-install-progress-bar" },
  { name: "pellets-meter-ok", className: "pf-pellets-meter pf-pellets-meter--ok" },
  { name: "pellets-meter-warn", className: "pf-pellets-meter pf-pellets-meter--warn" },
  { name: "pellets-meter-low", className: "pf-pellets-meter pf-pellets-meter--low" },
  // Runtime-constructed names (ProbeCard.tsx:35,40 build `pf-badge-${tone}`).
  // Tailwind's content scanner cannot see these. Harmless while they are
  // hand-written rules; a silently-missing style the moment anyone converts
  // them to utilities in JSX -- which this migration does not do.
  { name: "badge-ok", className: "pf-badge pf-badge-ok" },
  { name: "badge-warn", className: "pf-badge pf-badge-warn" },
];
