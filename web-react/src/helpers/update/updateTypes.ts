export interface UpdateState {
  version: string;
  branch: string;
  branches: string[];
  remote_url: string;
  remote_version: string;
  /** The served React bundle is older than the sources on disk -- a pull whose
   *  rebuild did not run, or failed. */
  web_ui_stale: boolean;
  /** The last rebuild attempt ended in failure. Independent of `web_ui_stale`:
   *  a forced rebuild of an already-current bundle can fail without leaving
   *  anything out of date. This is what puts the build log on offer. */
  web_ui_build_failed: boolean;
}

export interface UpdateCheck {
  current: string;
  behind: number;
}

export interface UpdateStatus {
  percent: number;
  status: string;
  output: string;
}

/** One incremental read of the web UI build's transcript. */
export interface BuildLog {
  text: string;
  offset: number;
  reset: boolean;
}

/** Started-flag returned by every mutation. */
export interface UpdateStarted {
  started: boolean;
}

/** Same envelope shape helpers/admin/adminApi.ts returns. */
export interface UpdateResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}
