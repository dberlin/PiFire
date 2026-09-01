import { describe, expect, it, rs } from "@rstest/core";
import { renderHook } from "@testing-library/react";

import { useDismissOnEscape } from "../../../src/helpers/useDismissOnEscape";

function press(key: string) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key }));
}

describe("useDismissOnEscape", () => {
  it("dismisses on Escape only while active, and stops listening once gone", () => {
    const onDismiss = rs.fn();
    const { rerender, unmount } = renderHook(
      ({ active }: { active: boolean }) => useDismissOnEscape(active, onDismiss),
      { initialProps: { active: true } },
    );

    press("Escape");
    expect(onDismiss).toHaveBeenCalledTimes(1);

    press("Enter");
    expect(onDismiss).toHaveBeenCalledTimes(1);

    // An overlay that is closed must not steal Escape from whatever sits
    // underneath it.
    rerender({ active: false });
    press("Escape");
    expect(onDismiss).toHaveBeenCalledTimes(1);

    // ...and an unmounted one must not leak a listener.
    rerender({ active: true });
    unmount();
    press("Escape");
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
