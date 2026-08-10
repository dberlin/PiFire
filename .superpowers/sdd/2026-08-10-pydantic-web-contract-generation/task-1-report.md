# Task 1 Report

## Result

DONE

Implementation commit: `3f94c69e5663ddd14a18e24f2a1a8123206ea4ee` (`ssrnmuzqymywypwwqurryzssmqvvvrsy`)

## Changed paths

- `common/web_contracts/__init__.py`
- `common/web_contracts/base.py`
- `common/web_contracts/export.py`
- `common/web_contracts/registry.py`
- `tests/unit/common/web_contracts/test_export.py`
- `web-react/package.json`
- `web-react/schema/contracts/manifest.json`
- `web-react/scripts/emitWebContracts.ts`
- `web-react/scripts/gen-types.ts`
- `.superpowers/sdd/2026-08-10-pydantic-web-contract-generation/task-1-report.md`

`web-react/src/helpers/contracts/` is created by write mode. It has no committed file yet because the explicit Task 1 bundle registry is empty; later bundle registrations produce committed `*.gen.ts` files there.

## RED evidence

Initial required command:

```bash
uv run pytest -q tests/unit/common/web_contracts/test_export.py
```

Observed collection failure before implementation:

```text
ModuleNotFoundError: No module named 'common.web_contracts'
1 error in 1.10s
```

Self-review regression RED for nested stale TypeScript output, using the same focused command:

```text
....F
FAILED test_typescript_write_removes_nested_unexpected_generated_files
1 failed, 4 passed in 1.39s
EXIT=1
```

The failure showed that write mode initially left `src/helpers/contracts/old/obsolete.gen.ts` behind.

## GREEN evidence

Final focused verification after the recursive stale-output fix:

```bash
uv run pytest -q tests/unit/common/web_contracts/test_export.py
```

```text
5 passed in 1.40s
EXIT=0
```

```bash
cd web-react
bun run gen:types
```

```text
Wrote web-react/schema/contracts/manifest.json
Wrote src/helpers/settings/settingsTypes.gen.ts
Wrote src/helpers/settings/controllerTypes.gen.ts
Wrote src/helpers/settings/settingsDefaults.gen.ts
EXIT=0
```

```bash
bun run gen:types:check
```

```text
Pydantic web contract artifacts are up to date.
src/helpers/settings/settingsTypes.gen.ts is up to date.
src/helpers/settings/controllerTypes.gen.ts is up to date.
src/helpers/settings/settingsDefaults.gen.ts is up to date.
Generated web contract TypeScript is up to date.
EXIT=0
```

```bash
bun run typecheck
```

```text
$ node node_modules/typescript7/bin/tsc -b
EXIT=0
```

A temporary smoke bundle (`smoke.schema.json` to `smoke.gen.ts`) exercised `compileFromFile` in both write and check modes; both exited 0. The normal generator then removed both temporary generated artifacts.

## Drift and determinism proof

The committed manifest bytes were temporarily changed from `{}\n` to `{ }\n`, then:

```bash
bun run gen:types:check
```

reported:

```text
changed: web-react/schema/contracts/manifest.json
EXIT=1
```

The mutated file SHA-256 was identical before and after check mode:

```text
BEFORE=1d6faa9e1a76d13f3ab8558a3640158b1f0a54f624a4e37ddc3ef41ed4191058
AFTER=1d6faa9e1a76d13f3ab8558a3640158b1f0a54f624a4e37ddc3ef41ed4191058
UNCHANGED=true
```

`bun run gen:types` restored the file and the following `bun run gen:types:check` exited 0. A second regeneration preserved the contract artifact digest exactly:

```text
BEFORE=ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356
AFTER=ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356
DETERMINISTIC=true
```

## Self-review

The owned-path diff contained no unrelated PID-SP or later-domain changes. Review found one write/check convergence bug for nested unexpected TypeScript files. A focused regression reproduced it, `filesBelow()` was made recursive, the test turned green, and re-review approved the fix with no remaining Critical or Important finding.

## Concerns

None.
