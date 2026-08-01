import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useModuleSwitch } from "../../../../src/helpers/wizard/useModuleSwitch";

const fetchModuleValues = rs.fn();
rs.mock("../../../../src/helpers/wizard/wizardApi", () => ({
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

describe("useModuleSwitch", () => {
  it("fetches module values and invokes apply once with them", async () => {
    fetchModuleValues.mockResolvedValue({ settings: { a: "1" }, config: {} });
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "display", errorMessage: "nope", apply }),
    );

    result.current.switchModule("mod2");

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply.mock.calls[0][0]).toEqual({ settings: { a: "1" }, config: {} });
    expect(apply.mock.calls[0][1]).toBe("mod2");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "display", "mod2");
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
  });

  it("sets the error message and never invokes apply when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "distance", errorMessage: "could not load", apply }),
    );

    result.current.switchModule("mod2");

    await waitFor(() => expect(result.current.error).toBe("could not load"));
    expect(apply).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it("clears a previous error when a new switch starts", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "display", errorMessage: "could not load", apply }),
    );
    result.current.switchModule("bad");
    await waitFor(() => expect(result.current.error).toBe("could not load"));

    fetchModuleValues.mockResolvedValue({ settings: {}, config: {} });
    result.current.switchModule("good");
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it("clears the selection without fetching when switched to a blank module", async () => {
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "display", errorMessage: "could not load", apply }),
    );

    result.current.switchModule("");

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply).toHaveBeenCalledWith({ settings: {}, config: {} }, "");
    expect(fetchModuleValues).not.toHaveBeenCalled();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });
});
