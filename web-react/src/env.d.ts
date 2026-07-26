/// <reference types="@rsbuild/core/types" />

interface ImportMetaEnv {
  readonly PUBLIC_DEMO?: string;
  /** Absolute backend origin for the BROWSER to call. Leave unset in dev so
   *  requests stay same-origin and go through rsbuild's proxy. */
  readonly PUBLIC_PIFIRE_URL?: string;
  /** The origin the dev server proxies to, injected by rsbuild.config.ts.
   *  DISPLAY ONLY -- never use it as a fetch base, or you reintroduce the
   *  cross-origin bypass that PUBLIC_PIFIRE_URL caused. */
  readonly PUBLIC_PIFIRE_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
