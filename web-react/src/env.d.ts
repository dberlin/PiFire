/// <reference types="@rsbuild/core/types" />

interface ImportMetaEnv {
  readonly PUBLIC_DEMO?: string;
  readonly PUBLIC_PIFIRE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
