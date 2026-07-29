export interface UpdateState {
  version: string;
  branch: string;
  branches: string[];
  remote_url: string;
  remote_version: string;
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
