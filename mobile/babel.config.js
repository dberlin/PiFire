// Without a babel.config.js, jest-expo's resolveBabelOptions falls back to
// the bare `babel-preset-expo` default, which enables
// `@babel/plugin-transform-runtime` and rewrites every file's helper
// functions (interopRequireDefault, etc.) into `require("@babel/runtime/...")`
// calls resolved relative to THAT FILE'S location. @pifire/core is a sibling
// workspace under packages/pifire-core, symlinked into mobile/node_modules,
// but Node resolves it by its real path when walking up for node_modules --
// so "@babel/runtime", which is only a dependency of mobile/, is never found
// (mobile/node_modules is not an ancestor of packages/pifire-core/src).
// Disabling the runtime-helpers transform makes Babel inline the helpers
// instead of importing them, which needs no such resolution and works no
// matter which workspace package the transformed file lives in.
module.exports = function (api) {
  api.cache(true);
  return {
    // Resolved the same way jest-expo's own no-babel-config fallback does
    // (resolveBabelOptions.js): "babel-preset-expo" is not hoisted into
    // mobile/node_modules directly (it's a transitive dependency of
    // jest-expo/expo, reachable only from their own resolution context), so
    // the bare string fails to resolve relative to this file. Resolving
    // through "expo/internal/babel-preset" -- itself a plain re-export of
    // babel-preset-expo -- works because "expo" IS a direct dependency here.
    presets: [[require.resolve("expo/internal/babel-preset"), { enableBabelRuntime: false }]],
  };
};
