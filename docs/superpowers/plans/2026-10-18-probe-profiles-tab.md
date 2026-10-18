# Probe Profiles Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore upstream's manual thermistor profile management in React Settings, including precise coefficient entry, safe edits, guarded deletion, and immediate availability to probe selectors.

**Architecture:** Keep the existing `probe_settings.probe_profiles` library and value-copied `probe_map.probe_info[].profile` contract. Add typed mutation endpoints backed by one production-owned profile service, also used by the tuner. The new settings tab reads the existing shared settings query; mutations invalidate it and revalidate the settings loader rather than introducing another profile cache.

**Tech Stack:** Python/Flask, Pydantic-generated JSON/TypeScript contracts, SQLite runtime persistence, React, React Router, TanStack Query, existing settings draft store.

**Spec:** Approved scope in this conversation (2026-10-18): restore the Probe Profiles tab recommended by the investigation, then implement it. The acceptance contract below records that scope. Historical context: `2026-07-26-react-probeconfig-page.md`, “Out of scope, deliberately,” deferred this editor while retaining the now-removed Flask page.

## Global Constraints

- Preserve profile storage shape: `{id, name, A, B, C}` keyed by stable ID. No settings schema bump, alternate file format, migration, or compatibility shim.
- Keep the existing tuner endpoint and its successful response shape. Both creation routes must call the same service, not copy its implementation.
- Coefficients are finite numbers, including zero and negative coefficients where supplied; do not invent calibration bounds. Accept scientific notation in the UI and preserve numeric precision without display rounding.
- Creating an unassigned profile and editing/deleting unused profiles do not affect control and may run in any mode. Applying or editing a profile referenced by a probe requires `Stop`, enforced server-side.
- Updating an assigned profile replaces every current probe-map copy with the same ID, including disabled probes, and queues `probe_profile_update`. Refusals write nothing.
- Deleting any profile referenced by any configured probe is refused. Display current usage and explain how to unassign it.
- Preserve existing controller, evidence, model, mutation-anchor, and release contracts. No deployment, commit, release, or push is requested.
- Work in the existing clean, unpublished Jujutsu working change above the release bookmark; do not create a raw Git worktree or move the bookmark.
- Tests/helpers must obey repository isolation rules. Agents skip build/lint/tests/formatters during edits; the integration owner runs verification after integration.

## Acceptance Contract

- Settings exposes `/settings/probe-profiles`, with a link from Probes.
- The tab lists existing profiles with full-precision coefficients and assigned probe names, allows Add/Edit, and confirms deletion.
- The form accepts name and A/B/C, rejects blank names and empty/non-finite coefficient fields, supports scientific notation, and preserves input on server/network errors.
- Unsaved profile drafts survive settings-tab navigation using the existing draft store. Discard is explicit.
- Successful saves are visible immediately in profile lists and probe selectors; tuner saves invalidate the same settings query.
- The backend preserves IDs on edits, generates IDs for additions, refuses missing IDs, propagates edits, guards live use, and blocks deletion in use.
- Applying a previously opened probe-map draft resolves known profile IDs against the current library so stale coefficient copies cannot undo a profile edit.
- Browser smoke covers create, edit, assign, propagated edit, deletion refusal, unused deletion, invalid input, and narrow-screen layout using an isolated temporary datastore, never grill hardware.

## Review Focus

1. A coefficient such as `9.51568577e-8` must round-trip without truncation; blank input must not become zero. Backend tests and browser smoke cover this.
2. An assigned profile can be referenced by several or disabled probes; all copies update, and any reference blocks deletion. Backend tests cover this.
3. A cook can start after the form opens; the server must refuse applying/editing an assigned profile without changing settings or queuing reload. Backend tests cover this independently of UI disabling.
4. A probe configuration draft can predate a profile edit; applying it must not restore old coefficients. A probe-map API regression covers this.
5. A save can fail or settings can refresh during editing; the form must retain the draft, and successful tuner saves must refresh selectors. Browser smoke and existing affected frontend behavior tests cover this.

---

## Contract and Consumer Inventory

- `ProfileInput` consumers: tuner endpoint, web contract registry/inventory, generated operation schemas/types, tuner API and save form, API tests.
- `tuner_profile`: Flask route registration is the dynamic consumer; LSP finds no direct Python callers. Existing clients and tests continue to call `/api/tuner/profile`.
- Settings navigation: `SETTINGS_TABS`, `SettingsShell`, `appRoutes`, draft key types, navigation/route tests and e2e settings fixtures.
- Profile persistence: `read_settings` / `write_settings`; settings migration retains user profile IDs; schema remains unchanged.
- Profile assignment: `PortForm`, probe reducer, `/api/probe_map`, wizard setup, tuner optional apply. The library remains authoritative for known IDs; unknown legacy embedded profiles are not deleted or silently replaced.
- Runtime consumption: `Controller.tick`, base mode update handling, `ProbesMain.update_probe_profiles`, per-device profile copies. Existing `probe_profile_update` is the reload mechanism; no controller return changes.
- Generated contracts: `common/web_contracts/operations.py`, `registry.py`, `inventory.py`, `common.web_contracts.export`, `web-react/scripts/gen-types.ts` and generated artifacts.
- Trace/replay, native artifacts, model restore/migrations, controller factories, evidence builders, mutation anchors, and experiments have no profile-management dependency or changed contract.

### Task 1: Profile mutations and runtime-safe propagation

**Files:**
- Create: `common/probe_profiles.py`
- Create: `blueprints/api_probe_profiles/__init__.py`, `blueprints/api_probe_profiles/routes.py`
- Modify: `app.py`, `blueprints/api_tuner/routes.py`, `blueprints/api/routes.py`
- Modify: `common/web_contracts/operations.py`, `common/web_contracts/registry.py`, `common/web_contracts/inventory.py`
- Create: `tests/web/test_api_probe_profiles.py`
- Modify affected existing tuner/probe-map and web-contract tests only where behavior changes.

**Interfaces:**
- Extract `ProfileValues(WireModel)` containing `name: str`, `a/b/c: FiniteNumber` and the current nonblank-name validator; `ProfileInput(ProfileValues)` retains `apply_to: str | None = None` unchanged on the wire.
- Export `ProbeProfileMutation(WireModel)` with `id: str`, `updated_probes: list[str]` (probe labels).
- `create_probe_profile(payload: ProfileInput) -> SavedProfile` generates/stores a profile, optionally applies it, and queues a reload only when applying.
- `update_probe_profile(profile_id: str, payload: ProfileValues) -> ProbeProfileMutation` preserves ID, updates every assigned copy, and queues reload when in use.
- `delete_probe_profile(profile_id: str) -> ProbeProfileMutation` removes only an unused profile; `updated_probes` is empty.
- Domain refusals carry machine code, HTTP status, and typed optional field/mode/detail. New routes use existing `api_response`/operation error conventions.
- `POST /api/probe_profiles`: `ProfileInput` -> envelope with `SavedProfile` data.
- `PUT /api/probe_profiles/<profile_id>`: `ProfileValues` -> envelope with `ProbeProfileMutation` data.
- `DELETE /api/probe_profiles/<profile_id>`: no request payload -> envelope with `ProbeProfileMutation` data.
- Errors: `bad_request` 400, `not_found` 404, `system_active` 409, `profile_in_use` 409. Error `data.field`, `data.mode`, and `data.detail` follow existing operation types.

- [x] Write behavior regressions first: precision-preserving create without probe changes; edit propagates to multiple references; edit while Hold refuses unchanged state; in-use deletion refuses; unused deletion succeeds; invalid/missing IDs and coefficients refuse; tuner apply queues reload and observes the same mode guard; stale probe-map copies resolve to current coefficients.
- [x] Run the initial focused regression before implementation and record failure due to missing endpoints.
- [x] Implement the domain service and endpoint registration. Use current persistence and control-delta conventions; preserve unrelated settings and avoid partial writes on validation/refusal.
- [x] Replace tuner creation body with the shared service, preserving validation and response compatibility. Remove obsolete Flask-handler comments rather than adding aliases.
- [x] Resolve known profile IDs to current library values when accepting a probe-map write; preserve unknown embedded legacy profiles and empty profile objects.
- [x] Register the new contracts and route inventory. Regenerate through `bun run gen:types`, not manual edits to generated files.
- [x] Run focused API, probe-map, and contract checks after integration. Expected: all pass with exact coefficients, stable IDs, safe control flags, and no writes on refusal.

### Task 2: Probe Profiles settings tab and cache integration

**Files:**
- Create: `web-react/src/components/settings/tabs/ProbeProfilesTab.tsx`
- Create: `web-react/src/helpers/probes/probeProfilesApi.ts`
- Modify: `web-react/src/components/appRoutes.tsx`, `web-react/src/helpers/settings/settingsTabs.ts`
- Modify: `web-react/src/components/settings/tabs/ProbesTab.tsx`, `web-react/src/components/settings/settings.css` as needed
- Modify: `web-react/src/components/tuner/ProfileForm.tsx`, `web-react/src/helpers/tuner/tunerApi.ts`
- Update affected existing frontend behavioral tests; remove incidental navigation-order/copy assertions rather than repinning them.

**Interfaces:**
- `ProbeProfilesTab()` consumes SettingsShell's `settings`, `mode`, and draft store, using `readLiveProfiles(settings)` and probe-map usage by stable profile ID.
- Typed client functions `createProbeProfile(ProfileInput)`, `updateProbeProfile(id, ProfileValues)`, `deleteProbeProfile(id)` use Task 1 endpoints and the existing API base URL convention. Non-success envelopes/network errors return readable error text without dropping form state.
- No additional GET route/cache: current library and usage come from the shared settings query.

- [x] Register `probe-profiles` as an editable settings tab and route, next to Probes; add “Manage probe profiles” navigation from Probes.
- [x] Implement full-precision list/usage display and Add/Edit form. Keep coefficient text as strings until validation; require nonempty finite values before numeric conversion. Keep ID separate from editable name.
- [x] Use the settings draft store for unsaved edits and explicit discard. Disable affected-profile mutations while not stopped, explain the requirement, and keep server refusals visible with the draft intact.
- [x] Implement unused-profile deletion with confirmation and readable in-use refusal. Keep buttons disabled while a mutation is pending.
- [x] On successful mutations invalidate `queryKeys.settingsRoot(BASE_URL)` and revalidate routes. Apply the same invalidation to the tuner's successful profile save; translate `system_active` clearly.
- [x] Update affected existing behavior tests and remove obsolete incidental assertions. The integration owner runs typecheck, targeted frontend tests, and browser smoke, not mid-flight agents.

### Task 3: Integrated verification and delivery

**Files:**
- Generated web contract artifacts from Task 1.
- Update this plan with execution evidence and concise operator instructions after smoke proof.

- [x] Run focused Python API/contract tests and frontend settings/tuner tests; run contract-generation check, Python lint, frontend typecheck/lint, and frontend build. Fix failures at their source. Unrelated existing formatting findings are recorded below, not restyled.
- [x] Launch the actual Flask application using temporary DB/log paths, `real_hw=False`, and a fresh default configuration. Use the built React UI, not mocked network responses.
- [x] In a browser create a scientific-notation profile; edit it; assign it through Probes; verify a subsequent edit updates assignment readback; verify in-use deletion refusal; unassign and delete; reject blank inputs; confirm draft retention across tabs and usable 390px layout. Exercise live-mode refusal through the API with isolated control state.
- [x] Request independent review of the whole change against the acceptance contract and Review Focus. Resolve significant findings and rerun affected verification.
- [x] Run the repository contract preflight. No release publication or push is authorized by this task.
- [x] Stop the isolated server, remove throwaway scripts, and record only actually observed verification. Leave implementation and plan in the working change.

## Execution Record

- Planning: investigation confirmed the missing UI in the actual built app. The old endpoint is absent; existing tuner creation accepts direct coefficients and the existing selector can assign them.
- Starting state: clean unpublished Jujutsu working change `6f305cb9`, parent `e35185c5` (`massive-reworks-and-new-ui`).
- Execution decision: backend and frontend have disjoint edit sets and fixed wire interfaces, so implement those slices concurrently; the integration owner owns generation, verification, review integration, and this record.
- Initial RED: `uv run pytest tests/web/test_api_probe_profiles.py -q --tb=short` produced four expected failures: create returned 404 and resource mutations returned 405 before implementation.
- Integration prerequisite: the contract inventory extractor did not discover encoded resource IDs in URL paths. Added a regression in `web-react/tests/unit/helpers/generatedContracts.test.ts`; it failed with an empty inventory instead of PUT/DELETE resource endpoints. Extended `web-react/scripts/extractWebTransports.ts` to represent encoded path parameters as `<>`, preserving the existing inventory normalization. This is required to register the new API honestly, not a bypass of contract coverage.
- Review fix 1: a map request could hold an older dispatcher settings snapshot while a profile edit committed. `test_map_apply_does_not_overwrite_profile_edit_after_request_snapshot` reproduced restoration of `9.515686e-8` instead of the newly saved `9.6e-8`. The map handler now rereads settings and performs guards, resolution, persistence, and reload enqueue in one transaction. Regression passes.
- Review fix 2: pending save ownership was component-local while drafts survived navigation. `ProbeProfilesTab.test.tsx` reproduced an editable pending form after tab remount. Pending/error state now travels with the shared draft; completion retires only the exact submitted draft. The regression verifies disabled editing/discard while pending and retained inputs plus error after refusal.
- Final Python suite: `uv run pytest -q --tb=short` — **9,995 passed, 5 skipped, 10 warnings**. Warnings came from the power-action guard tests (blocked shutdown/reboot attempts) and pygame image conversion deprecations.
- Final frontend suite: `bun run test` — **199 files, 2,006 tests passed**.
- Focused integration before review: **213 Python API/contract tests**, **497 frontend tests**, and TypeScript checking passed. After review: **119 profile/map/tuner API tests**, **6 profile/draft tests**, and **2 controller profile/map reload characterization tests** passed.
- Final contract preflight: **37 passed**. Pydantic/TypeScript generated-artifact check, frontend typecheck/build, Oxlint, and changed-file Python/frontend formatting checks passed.
- Repository-wide `bun run lint` reached formatting and reported ten untouched files after the two new modules were formatted: `src/components/shell/TimerBar.tsx`; dashboard `GrillGauge.test.tsx` / `ProbeCard.test.tsx`; settings `ProbesTab.test.tsx`; shell `Banners.test.tsx` / `TimerBar.controlCycle.test.tsx` / `TimerBar.test.tsx`; dashboard helpers `cookTime.test.ts` / `countdowns.test.ts`; timer `timerState.test.ts`. These unrelated files were left unchanged.
- Browser proof: actual built React + Flask, temporary SQLite/log directories, no controller hardware process. Created `9.51568577e-8` without rounding, edited and assigned the profile, verified propagated assignment readback and HTTP 409 on in-use deletion, unassigned/deleted it, rejected blank C, retained drafts across tabs, retained and displayed errors during offline-save recovery, and verified 390px form/list layout with document width exactly 390px. Repeated creation and failed-save/navigation recovery on the final rebuilt UI.
- Long-suite capture initially hit the MCP transport's 30-second timeout. Final complete suites were captured through managed background processes; no success claim relies on the timed-out Python run.
- No deployment, commit, bookmark movement, release publication, or push.

## Operator Instructions

Open **Settings → Probe Profiles**, or **Settings → Probes → Manage probe profiles**. Use **Add profile** to enter a name and the tuned A/B/C coefficients; scientific notation is accepted. Save, then select that profile in **Settings → Probes → Edit → Probe Profile** and save the probe configuration.

Editing a profile preserves its ID and updates every configured probe using it. Stop the grill before editing an assigned profile. Disabled probes still count as references. To delete a profile, choose another profile for every referencing probe, save the probe configuration, then confirm deletion in Probe Profiles.

The tuner continues to offer Save Only / Save & Apply and refreshes the same settings cache. No settings schema migration is needed. Existing release migrations may restore deleted built-in defaults on a future upgrade; user-created profiles retain the existing persistence behavior.

## Requested Minor Release

The subsequent publication request authorizes committing, pushing, and tagging through Jujutsu using `updater/tag_release.py`. Target: **v1.31.0, build 135**, derived from v1.30.4/build 134.

### Release prerequisite: native Jujutsu tags through the exact-revision gate

The updater's existing `--jj` mode invokes raw Git for tags and bypasses the authoritative push wrapper. Installed Jujutsu supports native lightweight tags; deployed updater discovery already uses `git describe --tags`.

**Files and interfaces:**
- `updater/tag_release.py` and `tests/unit/updater/test_tag_release.py`: native Jujutsu fetch, tag discovery/creation, reporting, and guarded publication; preserve the Git backend. Keep the release at `@` during verification and leave an empty child after successful publication.
- `scripts/exact_revision_gate.py` and `tests/unit/tools/test_exact_revision_gate.py`: optional `push --tag TAG` / `push_verified_bookmark(..., tag: str | None = None)`. Require the tag to identify the same revision as `@` and the release bookmark; publish both at the pinned operation, verify both remote targets, then revalidate durable evidence.
- No schema, required-command, preflight, revision-check, or evidence-admission changes.

**Acceptance and execution:**
- [x] Cover numeric tag ordering, existing-tag refusal, manifest mismatch, no raw Git in JJ mode, guarded publication failure, and `--no-push` without a false remote-publication claim.
- [x] Cover missing/wrong/moved tag targets, failed preflight/gate, pinned publication of both refs, and remote-target mismatch.
- [x] Exercise native tag discovery and release creation in disposable local Jujutsu repositories; run focused tests and independent review before publication.

**Publication procedure (results belong to immutable external gate evidence):**
1. Run `prek run --all-files`, commit the completed feature/tooling with Jujutsu, and invoke `uv run python updater/tag_release.py --jj --minor`.
2. The updater runs the authoritative separate preflight and five-command gate on the final release revision. Preserve evidence and logs; publish bookmark/tag only through its guarded push.
3. Confirm remote bookmark/tag, manifest version/build, and an empty Jujutsu working change above the published release. Do not edit the release to append evidence after verification.

**Prerequisite verification:**
- Focused updater/gate suite: **185 passed**; release-tooling Ruff checks and `prek run --all-files` passed.
- Native Jujutsu smoke in disposable repositories selected v1.31.0/build 135 over v1.9.6 and v1.30.4, preserved the old origin on `--no-push`, and left exactly one empty child over the tagged release. Duplicate-tag refusal preserved the manifest.
- The first native smoke caught Jujutsu's automatic empty-child creation when tagging makes `@` immutable. The updater now pins the full release ID and explicitly checks out that ID for verification; regression coverage models the automatic transition.
- Independent review identified invocation from a subdirectory incorrectly resolving the relative gate script. Publication and recovery commands now use `uv --directory <workspace-root>`. Actual subdirectory smoke reached the root gate path; an absent gate failed without claiming publication and left an empty child.
- Actual native local, pinned-operation, and origin tag resolution agreed on v1.30.4's full revision.

### Release-gate corrections

- Attempt `0a02a6056ee4249f91456d06dbd2e0b19b022baa` passed contract preflight and stopped at native rebuild: generated compiler identity files from CMake 4.4.3 and active 4.4.4 coexisted. Preserved the old generated directory outside the compiler-identity scan; no production code or gate checks were changed. Failed evidence remains intact.
- Attempt `db644f8600bc654fa45b5ecb2e933bf016991440` passed preflight, native rebuild, default Python, slow Python, and workspace tests. It stopped on 24 settings fidelity baselines that still described navigation before the new tab. No push occurred.
- Regenerated only the affected settings baselines. Review of old versus new data found no removed landmarks: existing desktop tabs only gained the new navigation landmark; phone tabs gained that landmark and 44px of navigation height. Probes additionally gained the management link and its intentional action wrapping. Comparison code, tolerances, and style checks are unchanged.
- Added profile-list and open-form fidelity/overflow cases at 1280×720 and 390×844 using the existing deterministic settings fixture. All **56 settings fidelity/overflow cases passed** without capture mode; E2E TypeScript checking passed.
- Rechecked the actual demo surface with the same read fixtures: all four form inputs and both actions were visible and usable; document widths equalled the 390px and 1280px viewports. This is layout verification only; the earlier real Flask smoke supplies mutation and persistence evidence.
