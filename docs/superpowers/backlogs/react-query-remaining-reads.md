# React Query - Remaining Work

Actionable work left after the shared settings, history, cook-file, recipe, metrics,
admin-state, and build-ID migrations. Completed findings and settled non-goals are
removed; plans and repository history carry that record.

**Last reconciled against live code: 2026-08-13.**

## Remaining fetch migrations

### Log-family discovery

`web-react/src/components/logs/EventsPage.tsx` still calls
`fetchLogFamilies()` from a cancelled mount effect. Give log-family discovery a
query key and move the read to React Query. Preserve the current empty-list behavior
while the request is pending or fails.

### Paginated recipe and cook-file lists

`web-react/src/components/recipes/RecipeList.tsx` and
`web-react/src/components/cookfiles/CookFileList.tsx` still own request-keyed effects
for `fetchFileListing()`. Move each listing behind a query keyed by
`page`, `perPage`, and `reverse`; retain the previous listing while a page change is
in flight.

### Probe-module catalog

`probeModulesLoader` in `web-react/src/helpers/probes/probeMapRoutes.ts` remains a
bare `getProbeModules()` read. Move it to `queryClient.fetchQuery()` with a key shared
by catalog consumers and invalidation paths.

### Wizard state

`wizardLoader` in `web-react/src/helpers/wizard/wizardRoutes.ts` remains a bare
`getWizardState()` read. Move the owner-level `WizardState` read behind React Query;
do not add child queries to `PortsCard`, `DevicesCard`, or the picker components.
Those components receive loader-owned state or issue explicit user-triggered scans.

## Behavioral follow-ups

### Show manual history-window refreshes

`HistoryPage` keeps the previous chart through `placeholderData` when the user changes
the history window, but only exposes `isPending`. Add a non-blocking `isFetching`
affordance so the old chart cannot look like the requested new window while the
request is in flight. Preserve the chart and drag-zoom reset behavior.

### Scope cook-file recovery state to the route parameter

`CookFilePage` keeps `recovering` and `recoverError` in component state while the
unkeyed `/cookfiles/:filename` route reuses the component across parameter changes.
A repair started for one file can therefore disable controls or show its result on
another file. Add a parameter-navigation regression, then key or reset recovery state
by `filename` without weakening query cancellation or stale-detail handling.

### Make settings read-failure semantics explicit

`getControllerMetadata()` converts transport failures to `null`, while `getMode()`
converts them to `""`; React Query caches both as successful values. Define the two
failure contracts at the settings API/query boundary and test them. Do not introduce
another sentinel convention or convert an intentional optional result into an error
without updating every consumer.

## Small correctness and maintenance fixes

### Remove deleted cook-file queries

After a successful deletion, `CookFileList` only invalidates
`queryKeys.cookfileRoot(file)`. Inactive detail/chart queries remain cached, so
back-navigation can render a deleted file until the 404 arrives. Remove the deleted
file's query family and update the deletion cache contract.

### Document `flushObservers()` fake-timer behavior

`web-react/tests/unit/test-utils.tsx::flushObservers()` awaits a real
`setTimeout(0)`. Document its fake-timer precondition and cover the failure-prone use
pattern; suites using fake timers must advance them rather than hang until timeout.

### Invalidate cleared history caches concurrently

`MaintenanceCard` awaits the metrics and history invalidations sequentially after
`clear_history`. Start both independent invalidations together with `Promise.all`,
then await `onChanged()` as today.
