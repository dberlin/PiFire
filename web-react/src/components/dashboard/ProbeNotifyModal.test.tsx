import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TargetEdit } from "../../helpers/notify/notifyState";
import { ProbeNotifyModal } from "./ProbeNotifyModal";

const OFF: TargetEdit = { enabled: false, target: 0, action: "none" };
const ON: TargetEdit = { enabled: true, target: 203, action: "none" };

function renderModal(over: Partial<Parameters<typeof ProbeNotifyModal>[0]> = {}) {
  const props = {
    open: true,
    probeName: "Brisket",
    isPrimary: false,
    units: "F" as const,
    initial: ON,
    saving: false,
    error: null,
    onSubmit: rs.fn(),
    onCancel: rs.fn(),
    ...over,
  };
  return { ...render(<ProbeNotifyModal {...props} />), props };
}

const master = () => screen.getByRole("checkbox", { name: /notify/i });
const number = () => screen.getByRole("spinbutton", { name: /target/i });
const slider = () => screen.getByRole("slider", { name: /target/i });

describe("ProbeNotifyModal", () => {
  it("renders nothing when closed", () => {
    const { container } = renderModal({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("titles itself with the probe name", () => {
    const { container } = renderModal();
    expect(container.querySelector(".pf-modal-title")).toHaveTextContent("Brisket Notifications");
  });

  it("reflects initial.enabled on the master switch", () => {
    renderModal({ initial: OFF });
    expect(master()).not.toBeChecked();
  });

  it("disables the target controls when the master switch is off", async () => {
    const user = userEvent.setup();
    renderModal();
    expect(number()).toBeEnabled();
    await user.click(master());
    expect(number()).toBeDisabled();
    expect(slider()).toBeDisabled();
  });

  // The four ranges are the Flask template's hard-coded ones
  // (_macro_dash_default.html:174-186), not probe.maxTemp from the payload.
  it("takes its slider range from targetRange", () => {
    const { unmount } = renderModal({ isPrimary: true, units: "F" });
    expect(slider()).toHaveAttribute("max", "600");
    unmount();
    renderModal({ isPrimary: false, units: "C" });
    expect(slider()).toHaveAttribute("max", "225");
  });

  it("binds the number input and the slider both ways", async () => {
    const user = userEvent.setup();
    renderModal();
    await user.clear(number());
    await user.type(number(), "203");
    expect(slider()).toHaveValue("203");
    fireEvent.change(slider(), { target: { value: "250" } });
    expect(number()).toHaveValue(250);
  });

  it("clamps a typed value into the probe's range", async () => {
    const user = userEvent.setup();
    renderModal({ isPrimary: false, units: "F" }); // food probe: 0-300
    await user.clear(number());
    await user.type(number(), "9999");
    expect(number()).toHaveValue(300);
  });

  // _macro_dash_default.html:188-198 renders the two action checkboxes only for
  // a non-Primary probe: shutting the grill down when the GRILL probe reaches
  // its target is what the setpoint is for.
  it("offers the action choice only for a food probe", () => {
    const { unmount } = renderModal({ isPrimary: false });
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByRole("radio", { name: /shutdown pifire/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /keep warm/i })).toBeInTheDocument();
    unmount();
    renderModal({ isPrimary: true });
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
  });

  it("submits the edited target and action", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({ initial: { enabled: true, target: 180, action: "none" } });
    await user.clear(number());
    await user.type(number(), "203");
    await user.click(screen.getByRole("radio", { name: /keep warm/i }));
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).toHaveBeenCalledWith({
      enabled: true,
      target: 203,
      action: "keepWarm",
    });
  });

  // Deliberate divergence from Flask, whose slider allows 0: the probe entry's
  // condition is "equal_above" (common/defaults.py:524), so a target of 0 fires
  // on the very next control pass -- and with "Shutdown PiFire" ticked that
  // means an instant shutdown.
  it("refuses to arm a target of 0 and says why", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({ initial: { enabled: true, target: 0, action: "none" } });
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/above 0/i);
  });

  it("submits a disabled edit even with a target of 0 -- that is how you turn it off", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({ initial: { enabled: false, target: 0, action: "none" } });
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).toHaveBeenCalledWith({ enabled: false, target: 0, action: "none" });
  });

  // Landmine 7: Flask's "Cancel" in this modal is not a close -- it POSTs a wipe
  // of the target AND both limit alerts (dash_default.js:803-831). React's
  // cancel closes and writes nothing; the master switch is how you disable.
  it("cancels on the button and on the scrim, and never submits", async () => {
    const user = userEvent.setup();
    const { container, props } = renderModal();
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    fireEvent.click(container.querySelector(".pf-modal-scrim") as Element);
    expect(props.onCancel).toHaveBeenCalledTimes(2);
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("disables the submit button while saving", () => {
    renderModal({ saving: true });
    expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled();
  });

  it("renders a save error", () => {
    renderModal({ error: "control write rejected" });
    expect(screen.getByRole("alert")).toHaveTextContent("control write rejected");
  });

  it("re-seeds from a changed `initial` while open", () => {
    const shared = { open: true, probeName: "Brisket", isPrimary: false, units: "F" as const };
    const { rerender } = render(
      <ProbeNotifyModal
        {...shared}
        initial={ON}
        saving={false}
        error={null}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    expect(number()).toHaveValue(203);
    rerender(
      <ProbeNotifyModal
        {...shared}
        initial={{ enabled: true, target: 165, action: "shutdown" }}
        saving={false}
        error={null}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    expect(number()).toHaveValue(165);
    expect(screen.getByRole("radio", { name: /shutdown pifire/i })).toBeChecked();
  });
});
