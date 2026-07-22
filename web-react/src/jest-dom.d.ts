import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";

declare module "@rstest/core" {
  // `Matchers<T>` is rstest's exported-but-empty extension point, inherited by
  // *both* the top-level `Assertion<T>` and the internal `Assertion_2<T>` that
  // chai's chained properties (e.g. `.not`) resolve to via `VitestAssertion`'s
  // `A[K] extends Chai.Assertion ? Assertion_2<T> : ...` mapping. Augmenting
  // `Assertion<T>` directly (as jest-dom's own vitest.d.ts does for vitest)
  // covers `expect(x).toBeInTheDocument()` but *not* `expect(x).not.toBeInTheDocument()`,
  // since `.not` type-resolves through Assertion_2, which doesn't extend the
  // augmented Assertion. Matchers<T> is reachable from both, so augment there.
  interface Matchers<T = unknown> extends TestingLibraryMatchers<unknown, T> {}
}
