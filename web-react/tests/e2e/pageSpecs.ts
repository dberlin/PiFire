import { stubAdmin, stubCookFiles, stubProbeModules, stubRecipes } from "./apiFixtures";
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
      // The card is a landmark; the FILL inside it is a separate one because it
      // is the only element carrying the hopper gradient, and the card cannot
      // see it. The gradient is new as of the 2026-07-26 colour ruling --
      // HopperView.color2 was deleted and dashboard.css:997 now derives the
      // light stop in place with color-mix -- so nothing has ever pinned it.
      // Task 14 converts dashboard.css, the largest file in the migration;
      // without this entry a wrong @apply on that rule would reach Task 15's
      // eyeball as the only cover. `background-image` is in STYLE_PROPS, so the
      // whole linear-gradient() is compared exactly.
      ".pf-dash-hopper-fill",
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
  // The two cook-file surfaces, and the ONLY cover components/cookfiles/
  // cookfiles.css has. That file landed after the Tailwind plan was written and
  // belongs to no task in it, so nothing else would notice a wrong @apply or a
  // preflight reset landing on it.
  //
  // Why the LIST is a separate spec rather than extra landmarks on `history`,
  // even though both measure /history: the landmark set is the KEY set of the
  // baseline file, so adding to `history` would force a recapture of
  // history-1280x720.json and history-390x844.json -- two of the 43 immutable
  // pre-Tailwind references the whole migration is measured against. A new spec
  // writes new files and leaves those two byte-identical.
  //
  // It also has to bring its own fixture (PageSpec.stubs) for exactly the same
  // reason: stubbing the listing inside stubApi() would change how many rows
  // /history's second section renders, and move `.pf-section#1` in those two
  // files. See apiFixtures.ts's stubCookFiles.
  {
    name: "cookfile-list",
    path: "/history",
    ready: ".pf-cf-table",
    root: ".pf-shell",
    stubs: stubCookFiles,
    landmarks: [
      ...SHELL,
      ".pf-cf-toolbar",
      ".pf-cf-toolbar-spacer",
      ".pf-cf-table",
      // The cell rule is measured directly, not inferred from the table box.
      // `.pf-cf-table th, .pf-cf-table td` sets padding, text-align and a
      // bottom border; a wrong text-align moves nothing the table can see, and
      // `border-bottom-*` is not in STYLE_PROPS at all.
      ".pf-cf-table th",
      ".pf-cf-table td",
      ".pf-cf-thumb-col",
      ".pf-cf-thumb",
      ".pf-cf-name",
      ".pf-cf-actions-col",
      ".pf-cf-row-actions",
      ".pf-cf-pager",
      ".pf-cf-pager-current",
    ],
  },
  {
    name: "cookfile-detail",
    path: "/cookfiles/2026-07-04-Brisket.pifire",
    // NOT `.pf-cf-meta`, which paints as soon as the detail payload lands.
    // CookFileChart fetches separately and swaps a one-line "Loading graph..."
    // hint for a 360px plot, so measuring before it arrives would record every
    // section below the chart at the wrong y. `.pf-history-chart` only exists
    // inside `{detail && ...}` AND after the chart request resolved, so it
    // proves both.
    ready: ".pf-history-chart",
    root: ".pf-shell",
    stubs: stubCookFiles,
    landmarks: [
      ...SHELL,
      ".pf-cf-meta",
      ".pf-cf-meta-thumb",
      ".pf-cf-meta-fields",
      ".pf-cf-dl",
      // `.pf-cf-dl dt` and `.pf-cf-dl dd` are their own rules (a dimmed colour
      // and a margin reset) that the grid container cannot report.
      ".pf-cf-dl dt",
      ".pf-cf-dl dd",
      ".pf-cf-table",
      ".pf-cf-row-actions",
      ".pf-cf-comment",
      ".pf-cf-comment-meta",
      ".pf-cf-comment-text",
      ".pf-cf-comment-assets",
      ".pf-cf-comment-thumb",
      ".pf-cf-media-grid",
      ".pf-cf-media-item",
      ".pf-cf-media-img",
      // A state class, and reachable only because the fixture's
      // metadata.thumbnail names one of its own assets -- MediaPanel adds the
      // modifier to whichever grid image is the current thumbnail. Without that
      // it would have needed a CHROME_PROBES entry, which would have moved an
      // immutable baseline.
      ".pf-cf-media-img--selected",
    ],
  },
  // The recipe browser, and the only cover components/recipes/recipes.css
  // has -- it landed after the Tailwind plan was written, like cookfiles.css,
  // so nothing else would notice a wrong @apply on it. Written out rather
  // than generated for the same reason as the cook-file specs above: it needs
  // its own fixture, since the demo server has no backend for either recipes
  // route at all.
  {
    name: "recipe-list",
    path: "/recipes",
    ready: ".pf-rcp-table",
    root: ".pf-shell",
    stubs: stubRecipes,
    landmarks: [
      ...SHELL,
      ".pf-rcp-toolbar",
      ".pf-rcp-toolbar-spacer",
      ".pf-rcp-table",
      // The cell rule, like .pf-cf-table's above: measured directly since
      // padding and text-align move nothing the table box itself can see.
      ".pf-rcp-table th",
      ".pf-rcp-table td",
      ".pf-rcp-thumb-col",
      ".pf-rcp-thumb",
      ".pf-rcp-link",
      ".pf-rcp-name",
      ".pf-rcp-actions-col",
      ".pf-rcp-row-actions",
      ".pf-rcp-pager",
      ".pf-rcp-pager-current",
    ],
  },
  {
    name: "recipe-detail",
    path: "/recipes/brisket.pfrecipe",
    // Unlike the cook-file detail page, nothing here fetches separately --
    // RecipeView renders every section synchronously off the same `detail`
    // state RecipePage holds. `.pf-rcp-step` is the last one added, from the
    // Program Steps section, so waiting on it still proves the whole page
    // painted.
    ready: ".pf-rcp-step",
    root: ".pf-shell",
    stubs: stubRecipes,
    landmarks: [
      ...SHELL,
      ".pf-rcp-about",
      ".pf-rcp-splash",
      ".pf-rcp-dl",
      // `.pf-rcp-dl dt` and `.pf-rcp-dl dd` are their own rules (a dimmed
      // colour, a margin reset) the grid container cannot report -- the same
      // reasoning as `.pf-cf-dl dt`/`dd` above.
      ".pf-rcp-dl dt",
      ".pf-rcp-dl dd",
      ".pf-rcp-stars",
      // Reachable only because the fixture's rating is non-zero, the same way
      // `.pf-cf-media-img--selected` depends on its fixture's thumbnail.
      ".pf-rcp-star--filled",
      ".pf-rcp-table",
      ".pf-rcp-asset-thumb",
      ".pf-rcp-ingredients-used",
      ".pf-rcp-step",
      ".pf-rcp-step-number",
      ".pf-rcp-run",
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
  // The twelfth settings tab, and the one that had no gate: it shipped after
  // the pre-Tailwind reference was captured, so it is absent from
  // SETTINGS_TABS above and from the 43 immutable baselines.
  //
  // It is written out here rather than added to that list because it needs
  // both things the generated specs cannot express: its own fixture (its route
  // loader is the only settings child with one), and the probes vocabulary
  // below. Those rules are what this spec is for -- probes.css scopes
  // .pf-probes-card and .pf-btn under .pf-probes-surface, and while the wizard
  // baselines cover the unscoped forms, nothing has ever measured the scoped
  // ones.
  {
    name: "settings-probes",
    path: "/settings/probes",
    ready: ".pf-probes-surface .pf-probes-card",
    root: ".pf-shell",
    stubs: stubProbeModules,
    landmarks: [
      ...SHELL,
      ...SETTINGS,
      ".pf-probes-surface",
      ".pf-probes-card",
      ".pf-probes-table",
      ".pf-btn",
      ".pf-modal-btn",
    ],
  },
  // The admin page. Written out rather than generated for the same reason as
  // the recipe and cook-file specs: it needs its own fixture, since the demo
  // server has no /api/admin backend and an unstubbed read would render the
  // failure branch -- a baseline of an error message, not of the page.
  //
  // `.pf-admin-note` is reachable here only because the fixture leaves the
  // pellet-database backup list empty; with two full lists the empty branch
  // would go unmeasured. `.pf-admin-notice` is NOT reachable at all -- it
  // appears only after a write lands -- so it is probed in CHROME_PROBES.
  {
    name: "admin",
    path: "/admin",
    //  The Logs card is rendered last, so its heading proves the whole page
    //  painted rather than just the shell and the first card.
    ready: 'h2:text-is("Logs")',
    root: ".pf-shell",
    stubs: stubAdmin,
    landmarks: [
      ...SHELL,
      ".pf-admin",
      ".pf-admin-error",
      ".pf-admin-header",
      ".pf-admin-title",
      ".pf-admin-mode",
      ".pf-admin-btn",
      ".pf-admin-card",
      ".pf-admin-card-title",
      ".pf-admin-wide",
      ".pf-admin-scroll",
      ".pf-admin-facts",
      ".pf-admin-fact",
      // The label and value own rules (a spaced uppercase label, a wrapping
      // value) that the .pf-admin-fact box cannot report, the same way
      // `.pf-cf-dl dt`/`dd` do above.
      ".pf-admin-fact-label",
      ".pf-admin-fact-value",
      ".pf-admin-actions",
      ".pf-admin-note",
      ".pf-admin-toggles",
      ".pf-admin-backup-group",
      ".pf-admin-backup-head",
      ".pf-admin-subtitle",
      ".pf-admin-backup-list",
      ".pf-admin-backup-row",
      ".pf-admin-backup-name",
      ".pf-admin-upload",
      // Borrowed from settings.css by the toggles and the upload picker, and
      // never before measured outside a .pf-settings ancestor.
      ".pf-field",
      ".pf-field-label",
      ".pf-input",
      ".pf-switch",
    ],
  },
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
  // Reachable only after an admin write lands, so no page baseline can cover
  // it: the admin spec never confirms an action, deliberately.
  { name: "admin-notice", className: "pf-admin-notice" },
];
