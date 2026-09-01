## Contract migration inventory

For every exclusion below, use the exact text `not applicable — no such consumer` and explain why.

- [ ] Constructors and builders were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Serializers were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Deserializers were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Trace replay and validation consumers were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Restore and migration consumers were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Smoke tools and operational scripts were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Mutation anchors were updated with matching source changes and remain unique, or: `not applicable — no such consumer` — explanation:
- [ ] Characterization tests were migrated without weakening golden behavior, or: `not applicable — no such consumer` — explanation:
- [ ] Experiments were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Direct and indirect call sites were migrated, or: `not applicable — no such consumer` — explanation:
- [ ] Shared current-contract builders import production constants and have no version override; historical migration inputs remain literal, or: `not applicable — no such consumer` — explanation:
- [ ] Shared test helpers live in non-collected helper modules, `conftest.py`, or `tests/fakes/`, or: `not applicable — no such consumer` — explanation:

## Public contract decision

- [ ] Explicit approved public-contract decision (required even when unchanged):
  - Decision and approver:
  - PID-SP `Controller.update()` raw signed-demand return: unchanged / approved change (choose one)
  - Allocation-bounded physical duty preserved: yes / approved change (choose one)

## Exact-revision evidence

- [ ] LSP references and structural search for dynamic controller consumers were reviewed before exported-symbol changes, or: `not applicable — no such consumer` — explanation:
- [ ] Focused contract preflight passed at the exact revision under review.
- [ ] All five required release commands passed, in order, at that same exact revision.
- [ ] Exact full revision (40 lowercase hexadecimal characters):
- [ ] Preserved local artifact directory (`.artifacts/exact-revision/<full-revision>/`):
- [ ] The artifact contains `evidence.json` plus every referenced stdout/stderr log and SHA-256 digest.
- [ ] No skipped, interrupted, timed-out, nonzero, missing, reordered, or revision-drifted command was accepted.
- [ ] Push used repository `jj push` or the direct PiFire exact-revision `push` wrapper; direct `jj git push` was not used.
