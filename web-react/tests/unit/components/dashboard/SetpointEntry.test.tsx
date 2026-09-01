import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SetpointEntry } from "../../../../src/components/dashboard/SetpointEntry";

/** The number the modal is currently showing. */
function box(): HTMLInputElement {
  return screen.getByLabelText("Set Hold Temperature") as HTMLInputElement;
}

describe("SetpointEntry", () => {
  it("calls onCancel on Escape", async () => {
    const user = userEvent.setup();
    const onCancel = rs.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={rs.fn()} onCancel={onCancel} />);
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders the initial value clamped into range", () => {
    render(<SetpointEntry open initial={1000} units="F" onSubmit={rs.fn()} onCancel={rs.fn()} />);
    expect(box().value).toBe("500");
  });

  it("steps the value up/down by the unit step, clamped at the range bounds", async () => {
    const user = userEvent.setup();
    render(<SetpointEntry open initial={498} units="F" onSubmit={rs.fn()} onCancel={rs.fn()} />);
    expect(box().value).toBe("498");

    await user.click(screen.getByRole("button", { name: "increase" }));
    expect(box().value).toBe("500"); // clamped at max, not 503

    for (let i = 0; i < 71; i++) {
      await user.click(screen.getByRole("button", { name: "decrease" }));
    }
    expect(box().value).toBe("150"); // clamped at min
  });

  it("submits the current value via onSubmit", async () => {
    const user = userEvent.setup();
    const onSubmit = rs.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={onSubmit} onCancel={rs.fn()} />);
    await user.click(screen.getByRole("button", { name: "Set Hold" }));
    expect(onSubmit).toHaveBeenCalledWith(225);
  });

  it("calls onCancel when the scrim is clicked", async () => {
    const user = userEvent.setup();
    const onCancel = rs.fn();
    const { container } = render(
      <SetpointEntry open initial={225} units="F" onSubmit={rs.fn()} onCancel={onCancel} />,
    );
    await user.click(container.querySelector(".pf-modal-scrim")!);
    expect(onCancel).toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------
  // Typing. The value used to be a <div>: only the steppers and the slider
  // could move it.
  // ---------------------------------------------------------------------

  it("accepts a typed temperature and submits it", async () => {
    const user = userEvent.setup();
    const onSubmit = rs.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={onSubmit} onCancel={rs.fn()} />);

    await user.clear(box());
    await user.type(box(), "310");
    await user.click(screen.getByRole("button", { name: "Set Hold" }));

    expect(onSubmit).toHaveBeenCalledWith(310);
  });

  it("lets a value be typed through digits that are individually out of range", async () => {
    const user = userEvent.setup();
    render(<SetpointEntry open initial={225} units="F" onSubmit={rs.fn()} onCancel={rs.fn()} />);

    await user.clear(box());
    await user.type(box(), "1"); // below the 150 floor, mid-word
    expect(box().value).toBe("1"); // NOT snapped to "150", which would strand the typist
    await user.type(box(), "80");
    expect(box().value).toBe("180");
  });

  it("owns an edited value while open and refreshes it only when reopened", async () => {
    const user = userEvent.setup();
    const props = { units: "F" as const, onSubmit: rs.fn(), onCancel: rs.fn() };
    const { rerender } = render(<SetpointEntry {...props} open initial={180} />);

    await user.clear(box());
    await user.type(box(), "310");
    rerender(<SetpointEntry {...props} open initial={181} />);
    expect(box().value).toBe("310");

    rerender(<SetpointEntry {...props} open={false} initial={190} />);
    rerender(<SetpointEntry {...props} open initial={190} />);
    expect(box().value).toBe("190");
  });

  it("clamps a typed over-range value once the field is left", async () => {
    const user = userEvent.setup();
    const onSubmit = rs.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={onSubmit} onCancel={rs.fn()} />);

    await user.clear(box());
    await user.type(box(), "9000");
    await user.tab();

    expect(box().value).toBe("500");
    await user.click(screen.getByRole("button", { name: "Set Hold" }));
    expect(onSubmit).toHaveBeenCalledWith(500);
  });

  it("keeps the last good value when the box is emptied, so submit cannot send NaN", async () => {
    const user = userEvent.setup();
    const onSubmit = rs.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={onSubmit} onCancel={rs.fn()} />);

    await user.clear(box());
    await user.click(screen.getByRole("button", { name: "Set Hold" }));

    expect(onSubmit).toHaveBeenCalledWith(225);
  });

  it("submits on Enter", async () => {
    const user = userEvent.setup();
    const onSubmit = rs.fn();
    render(<SetpointEntry open initial={225} units="F" onSubmit={onSubmit} onCancel={rs.fn()} />);

    await user.clear(box());
    await user.type(box(), "275{Enter}");

    expect(onSubmit).toHaveBeenCalledWith(275);
  });

  // ---------------------------------------------------------------------
  // The ceiling is the grill's, not a constant. settings.safety.maxtemp is
  // what the control loop shuts down above, and it is user-editable, so a
  // fixed 500 both barred reachable temperatures and offered unreachable ones.
  // ---------------------------------------------------------------------

  it("takes its ceiling from safetyMaxTemp, above the old fixed 500", async () => {
    const user = userEvent.setup();
    render(
      <SetpointEntry
        open
        initial={225}
        units="F"
        safetyMaxTemp={550}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );

    await user.clear(box());
    await user.type(box(), "540");
    await user.tab();
    expect(box().value).toBe("540"); // the old range would have snapped this to 500

    await user.clear(box());
    await user.type(box(), "560");
    await user.tab();
    expect(box().value).toBe("550"); // and still stops at the grill's limit
  });

  it("takes its ceiling from safetyMaxTemp when the grill's limit is the lower one", async () => {
    const user = userEvent.setup();
    render(
      <SetpointEntry
        open
        initial={225}
        units="F"
        safetyMaxTemp={400}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );

    await user.clear(box());
    await user.type(box(), "450");
    await user.tab();
    expect(box().value).toBe("400");
  });

  it("bounds the slider by the same ceiling", () => {
    const { container } = render(
      <SetpointEntry
        open
        initial={225}
        units="F"
        safetyMaxTemp={550}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    const slider = container.querySelector(".pf-setpoint-slider") as HTMLInputElement;
    expect(slider.max).toBe("550");
    expect(slider.min).toBe("150");
  });

  it("falls back to the fixed ceiling when safetyMaxTemp is absent or unusable", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <SetpointEntry open initial={225} units="F" onSubmit={rs.fn()} onCancel={rs.fn()} />,
    );
    await user.clear(box());
    await user.type(box(), "9000");
    await user.tab();
    expect(box().value).toBe("500");

    // A limit at or under the floor cannot bound anything; collapsing the range
    // onto it would leave the modal with one selectable temperature.
    rerender(
      <SetpointEntry
        open
        initial={225}
        units="F"
        safetyMaxTemp={0}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    await user.clear(box());
    await user.type(box(), "9000");
    await user.tab();
    expect(box().value).toBe("500");
  });
});
