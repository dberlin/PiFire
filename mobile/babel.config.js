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
    //
    // worklets: false -- babel-preset-expo (build/configs/expo.js) auto-adds
    // a worklet-transform plugin when `options.worklets !== false &&
    // options.reanimated !== false`: it FIRST tries to resolve
    // "react-native-worklets/plugin" and, only if that fails to resolve at
    // all (options.worklets left default), falls through to
    // "react-native-reanimated/plugin". react-native-worklets is only a
    // *peer* dependency of react-native-reanimated here (bun's isolated
    // linker installs it into react-native-reanimated's own node_modules,
    // not into mobile/node_modules or the workspace root), so
    // require.resolve("react-native-worklets/plugin", { paths: [projectRoot,
    // babel-preset-expo's own dir] }) returns null from mobile's context --
    // confirmed directly: `node -e "require.resolve('react-native-worklets/plugin')"`
    // from mobile/ throws MODULE_NOT_FOUND. Because the auto-detect is an
    // if/else (not two independent ifs), that failure does NOT fall through
    // to the reanimated branch -- it silently adds NO plugin at all, so
    // useAnimatedStyle/useAnimatedProps callbacks would never be
    // workletized (a runtime failure, not a build-time one).
    // Setting worklets:false forces the `else if (options.reanimated !==
    // false)` branch, which resolves "react-native-reanimated/plugin"
    // instead -- confirmed this DOES resolve and fully `require()`s from
    // mobile's context, because react-native-reanimated/plugin/index.js is
    // itself just `module.exports = require('react-native-worklets/plugin')`,
    // and that inner require resolves relative to react-native-reanimated's
    // OWN directory (where its peer dep react-native-worklets IS present),
    // not relative to mobile/ or babel-preset-expo/. Functionally identical
    // transform, reached through a path that actually resolves.
    presets: [
      [
        require.resolve("expo/internal/babel-preset"),
        { enableBabelRuntime: false, worklets: false },
      ],
    ],
  };
};
