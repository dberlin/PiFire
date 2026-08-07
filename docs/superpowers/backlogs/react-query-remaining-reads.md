# REST reads still on hand-rolled fetch

The 2026-08-07 plan put the settings blob, the history chart, cook files,
recipes, metrics, admin state and the build-id poll behind TanStack Query.
These were left alone, deliberately.

## Progress and stream state machines — probably leave as they are

`UpdatePage.tsx:91-106`, `wizard/InstallProgress.tsx:49`,
`TunerPage.tsx:96-120`, `logs/StreamingLogPanel.tsx:97`, `logs/LogViewer.tsx:68`.

Each polls toward a TERMINAL condition and fires side effects on specific
transitions -- UpdatePage sets `done`, clears its own interval from inside the
callback, and reloads the state the run changed. `useQuery` models a cache
entry, not a run. Converting these buys a `refetchInterval` callback and keeps
the state machine anyway.

## Plain mount reads — mechanical, follow MetricsPage

`EventsPage`, `RecipeList`, `CookFileList`, `PelletsPage`'s sub-reads,
`pellets/VocabTable`, `cookfiles/MediaPanel`, `cookfiles/CommentList`,
`recipes/IngredientsEditor`, `recipes/StepsEditor`,
`recipes/InstructionsEditor`, `recipes/RecipeAssetManager`,
`recipes/RecipeRunStatus`, `settings/tabs/ProbesTab`,
`settings/tabs/UnitsTab`, `tuner/ProfileForm`, `wizard/probes/PortsCard`,
`wizard/probes/DevicesCard`, `wizard/probes/ThermoworksPicker`,
`wizard/probes/BluetoothPicker`, `wizard/fields/UsbSerialPicker`,
`wizard/fields/I2cBusField`.

Recipe: `src/components/metrics/MetricsPage.tsx` for an envelope-returning API
(needs `unwrap`), `src/components/cookfiles/CookFilePage.tsx` for a throwing
one (does not). Add the key to `src/helpers/query/keys.ts`.

`admin/LogsCard`, `admin/BackupsCard` and `admin/SystemCard` were removed from
this list during Task 14 verification: Task 12 converted `AdminPage` itself
to `useQuery` (`queryKeys.adminState`), and all three cards only ever received
`state` fields as props from `AdminPage` -- none of them fetches on its own,
so there is nothing left in them to convert. `settings/tabs/ProbesTab` stays:
its `settings` prop is query-backed, but the probe module catalog it also
reads (`useLoaderData()`, wired through `probeModulesLoader` in
`helpers/probes/probeMapRoutes.ts`) still calls `getProbeModules()` as a plain
fetch on every route mount, revalidated only via React Router's
`revalidator.revalidate()`, never through the query cache.

## Not a candidate

The socket.io push plane -- `helpers/useLiveState.ts`,
`helpers/shellContext.ts`, `components/shell/AppShell.tsx`. Dash and pellet
data arrive by server push at a 1s cadence; there is no cache key, no
staleness and no refetch for react-query to manage.
