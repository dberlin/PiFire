# PiFire engineering rules

## Contract migrations

Treat every schema, trace, native artifact, evidence input, mutation anchor, and public controller return as a repository-wide contract.

Before modifying an exported symbol, use LSP references to inventory static consumers. Also use structural search for controller factories, registries, configuration-selected controllers, and other dynamic controller lookups that symbol references cannot discover. Do not edit until the migration inventory covers:

- constructors and builders;
- serializers and deserializers;
- trace replay and validation;
- model restore and migration paths;
- smoke tools and operational scripts;
- mutation anchors;
- characterization tests;
- experiments; and
- every direct and indirect call site.

Migrate every current-contract consumer in the same change. Shared builders describe the current contract only, import production-owned version constants, and expose no schema/version override. Historical migration inputs remain literal in the test that owns that history; never rewrite old payloads through a current builder.

Evidence remains strict. Never weaken evidence admission, durable cook identity, estimator seed requirements, replay validation, mutation expectations, golden assertions, or observable contracts to make fixtures pass. Preserve the exact mutation anchors `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` unless the matching production source actually changes; when it does, update the source and mutation entry together and keep each anchor unique.

Shared test helpers must live in `_`-prefixed non-test modules, `conftest.py`, or `tests/fakes/`. A collected test module must never import another collected test module.

Run the focused contract preflight before the five release commands:

```bash
uv run pytest tests/unit/test_no_cross_test_imports.py tests/unit/mpc/test_mutation_score.py tests/unit/common/test_current_contract_fixtures.py -q
```

The preflight is a prerequisite, not a sixth release command. Any failed, interrupted, or revision-drifted preflight fails the gate and leaves all five release commands not run. Preserve its stdout, stderr, and SHA-256 evidence separately from the five required command entries.

Only schema v2 evidence with the separate contract preflight can authorize a push. Preserved schema v1 evidence is historical-only and cannot authorize a push.

PID-SP's approved public contract is explicit: `Controller.update()` returns historical raw signed demand. `self.u`, `AllocationResult.auger_duty`, runner duty, and physical pulses remain allocation-bounded. Changing this public return requires a separately reviewed and explicitly approved public-contract decision; never clip the return incidentally while repairing physical duty.

## Exact-revision checks and artifacts

The exact-revision gate in `scripts/exact_revision_gate.py` is the sole command and evidence authority. Keep its revision checks before and after preflight and every release command, plus the final publication check. A skipped, interrupted, timed-out, nonzero, missing, reordered, or different-revision command fails closed.

Preserve `.artifacts/exact-revision/<full-revision>/evidence.json` and every referenced stdout/stderr log. CI uploads the directory as `exact-revision-<full-revision>`. Do not rename, delete, hand-edit, or reuse evidence from an earlier attempt.

## Jujutsu and prek

`jj new` and `jj describe` do not run Git commit hooks. Run commit-time checks explicitly with `prek run --all-files`; do not infer that a successful Jujutsu operation ran them. The `prek` pre-push hook is a convenience for Git clients only and is not Jujutsu enforcement.

Repository `jj push` must invoke PiFire's authoritative exact-revision `push` wrapper. Configure the repository-scoped alias with:

```bash
jj config set --repo aliases.push '["util", "exec", "--", "sh", "-c", "cd \"$JJ_WORKSPACE_ROOT\" && exec uv run python scripts/exact_revision_gate.py push --bookmark cumulative-mpc-learning --artifact-root .artifacts/exact-revision \"$@\"", "pifire-jj-push"]'
```

If the alias is unavailable, run the wrapper directly from the repository root:

```bash
uv run python scripts/exact_revision_gate.py push --bookmark cumulative-mpc-learning --artifact-root .artifacts/exact-revision
```

Direct `jj git push` intentionally bypasses the wrapper and is prohibited by project rules. Repository protection and the exact-revision CI status remain the shared, non-bypassable enforcement layer.
