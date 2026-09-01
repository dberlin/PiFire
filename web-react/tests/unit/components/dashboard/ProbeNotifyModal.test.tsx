import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProbeNotifyModal } from "../../../../src/components/dashboard/ProbeNotifyModal";
import type { LimitEdit, NotifyEdit, TargetEdit } from "../../../../src/helpers/notify/notifyState";

const OFF: TargetEdit = { enabled: false, target: 0, action: "none" };
const ON: TargetEdit = { enabled: true, target: 203, action: "none" };
const LIMIT_OFF: LimitEdit = { enabled: false, target: 0, action: "none" };
const EDIT: NotifyEdit = { target: ON, high: LIMIT_OFF, low: LIMIT_OFF };

function renderModal(over: Partial<Parameters<typeof ProbeNotifyModal>[0]> = {}) {
  const props = {
    open: true,
    probeName: "Brisket",
    isPrimary: false,
    units: "F" as const,
    initial: EDIT,
    saving: false,
    error: null,
    onSubmit: rs.fn(),
    onCancel: rs.fn(),
    ...over,
  };
  return { ...render(<ProbeNotifyModal {...props} />), props };
}

const master = () => screen.getByRole("checkbox", { name: /target temperature/i });
const highMaster = () => screen.getByRole("checkbox", { name: /high limit/i });
const lowMaster = () => screen.getByRole("checkbox", { name: /low limit/i });
const number = () => screen.getByRole("spinbutton", { name: /^target/i });
const slider = () => screen.getByRole("slider", { name: /^target temperature/i });
const highNumber = () => screen.getByRole("spinbutton", { name: /^high limit/i });
const highSlider = () => screen.getByRole("slider", { name: /^high limit temperature/i });
const lowNumber = () => screen.getByRole("spinbutton", { name: /^low limit/i });

describe("ProbeNotifyModal", () => {
  it("renders nothing when closed", () => {
    const { container } = renderModal({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("titles itself with the probe name", () => {
    const { container } = renderModal();
    expect(container.querySelector(".pf-modal-title")).toHaveTextContent("Brisket Notifications");
  });

  it("reflects initial.enabled on each master switch", () => {
    renderModal({
      initial: { target: OFF, high: { ...LIMIT_OFF, enabled: true }, low: LIMIT_OFF },
    });
    expect(master()).not.toBeChecked();
    expect(highMaster()).toBeChecked();
    expect(lowMaster()).not.toBeChecked();
  });

  it("disables the target controls when the master switch is off", async () => {
    const user = userEvent.setup();
    renderModal();
    expect(number()).toBeEnabled();
    await user.click(master());
    expect(number()).toBeDisabled();
    expect(slider()).toBeDisabled();
  });

  it("disables each limit's controls independently of the target", () => {
    renderModal({
      initial: { target: ON, high: { enabled: true, target: 550, action: "none" }, low: LIMIT_OFF },
    });
    expect(highNumber()).toBeEnabled();
    expect(lowNumber()).toBeDisabled();
  });

  // The four ranges are the Flask template's hard-coded ones
  // (_macro_dash_default.html:174-186), not probe.maxTemp from the payload. The
  // limit sliders take the SAME range (:220-232, :265-277).
  it("takes every slider's range from targetRange", () => {
    const { unmount } = renderModal({ isPrimary: true, units: "F" });
    expect(slider()).toHaveAttribute("max", "600");
    expect(highSlider()).toHaveAttribute("max", "600");
    unmount();
    renderModal({ isPrimary: false, units: "C" });
    expect(slider()).toHaveAttribute("max", "225");
    expect(highSlider()).toHaveAttribute("max", "225");
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

  // _macro_dash_default.html:188-198 renders the two target action checkboxes
  // only for a non-Primary probe: shutting the grill down when the GRILL probe
  // reaches its target is what the setpoint is for.
  it("offers the target action choice only for a food probe", () => {
    const { unmount } = renderModal({ isPrimary: false });
    const group = screen.getByRole("group", { name: /when it is reached/i });
    expect(within(group).getAllByRole("radio")).toHaveLength(3);
    expect(within(group).getByRole("radio", { name: /shutdown pifire/i })).toBeInTheDocument();
    expect(within(group).getByRole("radio", { name: /keep warm/i })).toBeInTheDocument();
    unmount();
    renderModal({ isPrimary: true });
    expect(screen.queryAllByRole("group", { name: /when it is reached/i })).toHaveLength(0);
  });

  // THE ruling on the Flask asymmetry (_macro_dash_default.html:238-244,
  // :284-308), ported deliberately rather than flattened. "Shutdown PiFire" on
  // a high limit is a runaway-heat cutoff and "Attempt Re-ignite" on a low limit
  // is a fire-out response: both are statements about the FIRE, which only the
  // primary probe measures. A food probe reading low means cold meat, not a dead
  // fire -- arming a re-ignite there fires the moment cold food goes on the
  // grate, and pre-arming cannot help, because the temperature genuinely leaves
  // the range and comes back.
  it("offers the limit actions only for the primary probe", () => {
    const { unmount } = renderModal({ isPrimary: false });
    expect(screen.queryAllByRole("group", { name: /high limit/i })).toHaveLength(0);
    expect(screen.queryAllByRole("group", { name: /low limit/i })).toHaveLength(0);
    unmount();
    renderModal({ isPrimary: true });
    const high = screen.getByRole("group", { name: /above the high limit/i });
    const low = screen.getByRole("group", { name: /below the low limit/i });
    // No re-ignite on the high limit: the socket payload publishes no
    // highLimitReignite (blueprints/mobile/socket_io.py:781-787), so there would
    // be no way to show the state back.
    expect(
      within(high)
        .getAllByRole("radio")
        .map((r) => r.getAttribute("value")),
    ).toEqual(["none", "shutdown"]);
    expect(
      within(low)
        .getAllByRole("radio")
        .map((r) => r.getAttribute("value")),
    ).toEqual(["none", "shutdown", "reignite"]);
  });

  // Every probe still gets both limit TEMPERATURE controls -- that half of the
  // Flask asymmetry is ported as-is (_macro_dash_default.html:216-234): being
  // told a food probe has run hot is useful on any probe; acting on the fire is
  // not.
  it("offers both limit temperatures on every probe", () => {
    renderModal({ isPrimary: false });
    expect(highNumber()).toBeInTheDocument();
    expect(lowNumber()).toBeInTheDocument();
  });

  it("submits the edited target and action", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({
      initial: { ...EDIT, target: { enabled: true, target: 180, action: "none" } },
    });
    await user.clear(number());
    await user.type(number(), "203");
    await user.click(screen.getByRole("radio", { name: /keep warm/i }));
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).toHaveBeenCalledWith({
      target: { enabled: true, target: 203, action: "keepWarm" },
      high: LIMIT_OFF,
      low: LIMIT_OFF,
    });
  });

  it("submits all three sections in one edit", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({ isPrimary: true, initial: { ...EDIT, target: OFF } });
    await user.click(highMaster());
    await user.clear(highNumber());
    await user.type(highNumber(), "550");
    await user.click(lowMaster());
    await user.clear(lowNumber());
    await user.type(lowNumber(), "150");
    const low = screen.getByRole("group", { name: /below the low limit/i });
    await user.click(within(low).getByRole("radio", { name: /re-ignite/i }));
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).toHaveBeenCalledWith({
      target: OFF,
      high: { enabled: true, target: 550, action: "none" },
      low: { enabled: true, target: 150, action: "reignite" },
    });
  });

  // One choice, not two checkboxes: the backend runs
  // `if shutdown ... elif keep_warm ... elif reignite`
  // (notify/notifications.py:157-174), so an entry carrying both drops the
  // re-ignite. Flask enforces this with JavaScript that unchecks the other box
  // (_macro_dash_default.html:294-308); a radio group cannot express it at all.
  it("cannot arm shutdown and re-ignite on the same limit", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({
      isPrimary: true,
      initial: { ...EDIT, low: { enabled: true, target: 150, action: "reignite" } },
    });
    const low = screen.getByRole("group", { name: /below the low limit/i });
    await user.click(within(low).getByRole("radio", { name: /shutdown pifire/i }));
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ low: { enabled: true, target: 150, action: "shutdown" } }),
    );
  });

  // Deliberate divergence from Flask, whose slider allows 0: the probe entry's
  // condition is "equal_above" (common/defaults.py:536), so a target of 0 fires
  // on the very next control pass -- and with "Shutdown PiFire" ticked that
  // means an instant shutdown.
  it("refuses to arm a target of 0 and says why", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({
      initial: { ...EDIT, target: { enabled: true, target: 0, action: "none" } },
    });
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/above 0/i);
  });

  // Same guard, same reason, on both limits: a HIGH limit of 0 is "equal_above"
  // 0 and fires immediately; a LOW limit of 0 is "equal_below" 0 and can never
  // fire at all, which is an alert that silently does nothing.
  it("refuses to arm a limit of 0 and names which one", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({
      initial: { ...EDIT, high: { enabled: true, target: 0, action: "none" } },
    });
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/high limit/i);
  });

  it("submits a disabled edit even with a target of 0 -- that is how you turn it off", async () => {
    const user = userEvent.setup();
    const { props } = renderModal({ initial: { target: OFF, high: LIMIT_OFF, low: LIMIT_OFF } });
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(props.onSubmit).toHaveBeenCalledWith({
      target: OFF,
      high: LIMIT_OFF,
      low: LIMIT_OFF,
    });
  });

  // Flask's "Cancel" in this modal is not a close -- it POSTs a wipe
  // of the target AND both limit alerts (dash_default.js:803-831). React's
  // cancel closes and writes nothing; the master switches are how you disable.
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
        initial={EDIT}
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
        initial={{
          target: { enabled: true, target: 165, action: "shutdown" },
          high: { enabled: true, target: 275, action: "none" },
          low: LIMIT_OFF,
        }}
        saving={false}
        error={null}
        onSubmit={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    expect(number()).toHaveValue(165);
    expect(highNumber()).toHaveValue(275);
    expect(screen.getByRole("radio", { name: /shutdown pifire/i })).toBeChecked();
  });
});
