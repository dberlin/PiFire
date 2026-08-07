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

## Follow-ups from the branch review

Found during the 2026-08-07 whole-branch review, after the 14-task migration
was otherwise green. Three findings were fixed on the spot (the flushObservers
race, clear_history's missing invalidation, and the four queryClient-singleton
violations); these nine were not.

1. **`unwrap()` drops the envelope beyond `message`/`status`.**
   `helpers/query/unwrap.ts:41` throws `new ApiError(r.message, r.status)` from
   a `ResultEnvelope<T>` on `!r.ok`. Any other structured field the envelope
   carried on failure -- the way `AdminResult`/write paths carry a `field` for
   validation errors -- never reaches the thrown `ApiError`, because `unwrap`
   is only wired to `ok`/`status`/`message`/`data`. No read path has needed
   more than that yet, which is why this was not fixed now; it becomes real
   the first time a `useQuery` fetcher wraps an envelope whose failure a caller
   needs to branch on by more than the message text.

2. **`HistoryPage` gives no in-flight affordance during a manual window
   change.** `historyChart(minutes)` swaps queryKey the instant the Minutes
   control changes, and `placeholderData: (previous) => previous`
   (`HistoryPage.tsx:58-63`) keeps the OLD window's chart on screen while the
   new one loads -- which is correct for auto-refresh polls, but leaves a
   manual window change with no loading cue at all: the chart just sits still
   until the new data swaps in. `isFetching` (distinct from `isPending`) is
   sitting unused in the same `useQuery` result and would drive a small
   indicator without disturbing the placeholder behaviour auto-refresh relies
   on.

3. **`SettingsShell` has a third unguarded accent-seeding site.** FIX 1 fixed
   the false-green test around `AppPrefs.tsx` and `TunerPage.tsx`'s render-phase
   seeding idiom, and confirmed by breaking each implementation that both were
   real bugs, not just suspect. `SettingsShell` seeds an accent the same
   general shape (settings-derived local state) but was not audited against
   the same "does a live settings refetch clobber an in-progress user choice"
   question this branch asked of the other two. Needs the same
   break-the-implementation treatment before it can be called clean, not an
   assumption that three call sites sharing a pattern share a bug.

4. **Two flaky test files need a real diagnosis, not just a re-run.**
   `useSaveSettings.test.tsx` and `settingsDrafts.test.tsx` were both observed
   flaking independently of this branch's changes (re-run clean before
   investigating further, per this review's own baseline notes) -- FIX 3
   touched `useSaveSettings.test.tsx`'s `renderWithLoader()` to add a
   `QueryClientProvider`, which is a plausible new interaction with whatever
   was already flaky there (a timing race between the router's loader and the
   query cache, most likely) and raises this from "known pre-existing, not
   mine" to "known pre-existing, now touched by mine, still not diagnosed."
   Neither file's flake was root-caused this round.

5. **`CookFilePage`'s `recoverError`/`recovering` state is not scoped to a
   filename.** A user who opens a broken cook file, starts Attempt Repair,
   then navigates to a DIFFERENT broken cook file before the first repair
   settles would see the second page's repair banner reflect the first
   file's in-flight/error state, because that state is plain `useState` on
   the component rather than keyed by `filename` the way the query cache
   already is. Whether this is reachable in practice depends on whether
   `CookFilePage` remounts on a filename-only route change (React Router
   route param changes do not always force a remount) -- that reachability
   question was not run down this round.

6. **`CookFileChart` did not drop stale data on a failed refetch.** ~~Left as a
   backlog item~~ Fixed inline during this review, since it turned out to be
   exactly the one-line, cleanly-testable case the task allowed folding into
   FIX 2's revision: `chart = data ? toCookChartInput(data) : null` rendered
   the PREVIOUS successful payload behind the "Couldn't load this cook's chart
   data" banner, because react-query does not clear `data` on a failed
   refetch -- only `error` changes. Now reads `data && !failed` (source:
   `src/components/cookfiles/CookFileChart.tsx`). Proven both directions: a
   new test (`tests/unit/components/cookfiles/CookFileChart.test.tsx` ::
   "drops the old chart once a refetch fails, rather than leaving it behind
   the error banner") fails against the one-line-reverted implementation with
   `expected element not to be in the document` on the chart test id, and
   passes against the fix.

7. **A third, undocumented error-handling convention: fail-closed sentinels.**
   The codebase already has two documented shapes -- the `ResultEnvelope`
   write paths resolve rather than throw (`ok`/`status`/`message`/`data`), and
   `unwrap()` converts an envelope into a thrown `ApiError` at the `useQuery`
   boundary. `settingsApi.ts`'s `getMode` and `getControllerMetadata` are
   neither: both catch their own failures and return a plain sentinel value
   instead -- `getMode` returns `""` on any failure, deliberately fail-CLOSED
   (`settingsApi.ts:59-65`: "'' means UNKNOWN, and consumers gate on it...
   when we cannot confirm the grill is stopped we must not allow the
   change"), while `getControllerMetadata` returns `null`, deliberately
   fail-OPEN (`settingsApi.ts:48`: "Controller tab renders an 'unavailable'
   state"). Both are called through `queryClient.fetchQuery` in
   `settingsRoutes.ts:41-45`, so a consumer reading `useQuery`/loader data has
   no way to tell "confirmed empty" from "the request failed" without already
   knowing which of these two opposite conventions that particular field
   follows. Worth a name and a shared helper before a third caller invents a
   third opposite default.

8. **`HistoryPage.test.tsx` asserted a deleted mechanism as current fact.**
   Fixed inline (one line, comment-only, exactly as flagged): the test at
   "does not stack a poll on top of a request that is still in flight" said
   "The in-flight guard is the page's own request id (via `loading`), not a
   second mechanism" -- true of the pre-migration hand-rolled fetch, but
   `HistoryPage.tsx:55-57`'s own comment says the opposite post-migration:
   "the in-flight guard the old effect hand-rolled (the `loading` dependency)
   has nothing left to do," because react-query's own per-key request dedup
   is what now absorbs a `refetchInterval` tick against an outstanding fetch.
   The comment now says that instead of the deleted mechanism.

9. **The fidelity screenshots have never been run.** `tests/e2e/*.spec.ts`
   (referenced by name in several source comments this branch and prior tasks
   added, e.g. `dashboard-reflow.spec.ts`) assert pixel-level layout claims
   that jsdom-based unit tests explicitly disclaim ("jsdom does no layout, so
   this file deliberately asserts NOTHING about geometry" --
   `dashboardStyles.test.tsx`). Nothing in this review's verification pass, or
   evidently any prior task's, actually launched Playwright against a real
   browser to confirm those claims hold. The gate exists in name only until
   someone runs it once and records the result.
