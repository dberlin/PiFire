import { describe, expect, it } from "@rstest/core";

import { createQueryClient } from "../../../../src/helpers/query/queryClient";

describe("createQueryClient", () => {
  it("does not retry: a failed read is rendered in place, not silently re-attempted", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.retry).toBe(false);
  });

  it("does not refetch on window focus: the live plane is socket-push, not polled", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.refetchOnWindowFocus).toBe(false);
  });

  it("holds a read fresh long enough for sibling pages to share it", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.staleTime).toBe(30_000);
  });
});
