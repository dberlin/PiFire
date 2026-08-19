module.exports = {
  preset: "jest-expo",
  // react-native-reanimated v4 delegates its native binding to
  // react-native-worklets, whose src/WorkletsModule/NativeWorklets.native.ts
  // calls into a real TurboModule at import time (`loadUnpackers`) -- that
  // module does not exist under Jest's Node environment, so a bare `import
  // "react-native-reanimated"` throws immediately (confirmed: this is what
  // Step 4 of TDD hit here before this config existed). Mapping
  // "react-native-reanimated" to its own "/mock" entry point is NOT enough
  // on its own: mock.ts re-exports test helpers (setUpTests,
  // advanceAnimationByFrame, ...) straight from the real "./index", so
  // without also fixing resolution, that real index still pulls in the same
  // NativeWorklets.native.ts and crashes the same way (confirmed too).
  // react-native-worklets ships the actual fix for this as
  // "jest/resolver.js": it strips the "native" condition when resolving
  // anything under react-native-worklets, so `.native.ts` files
  // (NativeWorklets.native.ts) resolve to their plain, non-native sibling
  // (NativeWorklets.ts) instead -- a Jest-safe implementation with no
  // TurboModule access. This is the package's own documented Jest
  // integration point, not a project-specific workaround.
  resolver: "react-native-worklets/jest/resolver.js",
  moduleNameMapper: {
    "^react-native-reanimated$": "react-native-reanimated/mock",
  },
  // @testing-library/react-native@14 bundles its jest matchers by default
  // and no longer ships an "extend-expect" entry point (removed since v12.4),
  // so there is nothing to list here for the installed version.
  // @pifire/core ships TypeScript source, and Expo's preset does not
  // transform node_modules by default.
  //
  // The leading "\\.bun/" alternative is a bun-specific addition on top of
  // the brief's pattern: bun's content-addressable store puts every package
  // under node_modules/.bun/<name>@<version>+<hash>/node_modules/<name>/...,
  // so the *first* "node_modules/" segment a path-matching regex finds is
  // immediately followed by ".bun", not the real package name. Without this
  // alternative, RegExp#test's leftmost-match semantics stop right there —
  // ".bun" never satisfies the package allowlist below, so the whole
  // subpattern matches at that outer segment and every package (including
  // @react-native/jest-preset, whose own setup.js ships untranspiled ESM and
  // must run through babel-jest) is wrongly treated as ignored. Excluding
  // ".bun/" here defers the match to the real nested "node_modules/<name>/"
  // segment, where the package-name allowlist below applies as intended.
  transformIgnorePatterns: [
    "node_modules/(?!(\\.bun/|(jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@pifire/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg))",
  ],
};
