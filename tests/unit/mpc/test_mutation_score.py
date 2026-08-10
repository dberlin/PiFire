"""Mutation-score definitions must remain runnable, not silently skipped."""

from tools.experiments import mutation_score


def test_every_listed_mutation_anchor_occurs_exactly_once():
    """A stale source anchor must fail the contract before `main()` can skip it."""
    missing_or_ambiguous = [
        (label, path.name, path.read_text().count(old))
        for label, path, old, _new in mutation_score.MUTATIONS
        if path.read_text().count(old) != 1
    ]
    assert not missing_or_ambiguous, f"mutation anchors must occur exactly once: {missing_or_ambiguous}"
