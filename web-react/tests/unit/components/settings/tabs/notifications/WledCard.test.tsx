import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WledCard } from "../../../../../../src/components/settings/tabs/notifications/WledCard";
import {
  discoverWled,
  pushWledProfiles,
  testWledProfile,
} from "../../../../../../src/helpers/notify/wledApi";

rs.mock("../../../../../../src/helpers/notify/wledApi", () => ({
  discoverWled: rs.fn(),
  pushWledProfiles: rs.fn(),
  testWledProfile: rs.fn(),
}));

afterEach(cleanup);

// A full wled bag, including the legacy mode_presets/event_presets keys the
// card must NOT render but MUST preserve on edit.
const wledFixture = () => ({
  enabled: true,
  device_address: "wled.local",
  use_profiles: true,
  use_suggested_presets: false,
  notify_duration: 120,
  profile_numbers: {
    idle: 200,
    booting: 201,
    preheat: 202,
    cooking: 203,
    cooldown: 204,
    target_reached: 205,
    overshoot_alarm: 206,
    probe_alarm: 207,
    low_pellets: 208,
    timer_done: 209,
    error_fault: 210,
    night_mode: 211,
  },
  mode_presets: { Stop: 1, Startup: 1, Reignite: 1, Smoke: 1, Hold: 1, Shutdown: 1, Prime: 1 },
  event_presets: {
    Temp_Achieved: 1,
    Recipe_Next: 1,
    Grill_Error: 1,
    Pellet_Level_Low: 1,
    Timer_Expired: 1,
  },
  suggested_config: { cooking_color: "blue", idle_brightness: 20, night_mode: false, led_count: 6 },
});

describe("WledCard editor fields", () => {
  it("renders all 12 profile-number rows when use_profiles is on", () => {
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    for (const label of [
      "idle",
      "booting",
      "preheat",
      "cooking",
      "cooldown",
      "target_reached",
      "overshoot_alarm",
      "probe_alarm",
      "low_pellets",
      "timer_done",
      "error_fault",
      "night_mode",
    ]) {
      expect(screen.getByLabelText(new RegExp(`^${label}$`, "i"))).toBeInTheDocument();
    }
  });

  it("does NOT render mode_presets/event_presets keys", () => {
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    expect(screen.queryByLabelText(/Reignite/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Temp_Achieved/i)).not.toBeInTheDocument();
  });

  it("editing a profile number calls onChange with that row changed AND mode_presets preserved", () => {
    const onChange = rs.fn();
    render(<WledCard wled={wledFixture()} onChange={onChange} />);
    const cooking = screen.getByLabelText(/^cooking$/i) as HTMLInputElement;
    fireEvent.change(cooking, { target: { value: "222" } });
    const next = onChange.mock.calls.at(-1)?.[0];
    expect(next.profile_numbers.cooking).toBe(222);
    expect(next.profile_numbers.idle).toBe(200); // sibling intact
    expect(next.mode_presets).toEqual(wledFixture().mode_presets); // parity boundary preserved
  });

  it("hides the profile grid when use_profiles is off", () => {
    render(<WledCard wled={{ ...wledFixture(), use_profiles: false }} onChange={rs.fn()} />);
    expect(screen.queryByLabelText(/^cooking$/i)).not.toBeInTheDocument();
  });

  it("shows suggested-config fields only when use_suggested_presets is on", () => {
    const { rerender } = render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    expect(screen.queryByLabelText(/idle brightness/i)).not.toBeInTheDocument();
    rerender(
      <WledCard wled={{ ...wledFixture(), use_suggested_presets: true }} onChange={rs.fn()} />,
    );
    expect(screen.getByLabelText(/idle brightness/i)).toBeInTheDocument();
  });
});

describe("WledCard actions", () => {
  it("Push and Test are blocked with an inline error when the address is empty", async () => {
    render(<WledCard wled={{ ...wledFixture(), device_address: "" }} onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /push profiles/i }));
    expect(await screen.findByText(/enter a wled device address/i)).toBeInTheDocument();
    expect(pushWledProfiles).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /test profile/i }));
    expect(testWledProfile).not.toHaveBeenCalled();
  });

  it("Push sends the live device_address and profile_numbers, then shows success", async () => {
    (pushWledProfiles as ReturnType<typeof rs.fn>).mockResolvedValue({
      result: "success",
      message: "done",
      profiles_pushed: 12,
    });
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /push profiles/i }));
    expect(pushWledProfiles).toHaveBeenCalledWith(
      "wled.local",
      expect.objectContaining({ cooking: 203, idle: 200 }),
    );
    expect(await screen.findByText(/12/)).toBeInTheDocument();
  });

  it("Test sends the cooking profile number", async () => {
    (testWledProfile as ReturnType<typeof rs.fn>).mockResolvedValue({
      result: "success",
      message: "ok",
    });
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /test profile/i }));
    expect(testWledProfile).toHaveBeenCalledWith("wled.local", 203);
  });

  it("Discover renders a pick-list and Use fills the device address", async () => {
    (discoverWled as ReturnType<typeof rs.fn>).mockResolvedValue({
      result: "success",
      message: "Found 1",
      devices: [{ ip: "10.0.0.9", led_count: 30, name: "WLED-Kitchen" }],
    });
    const onChange = rs.fn();
    render(
      <WledCard wled={{ ...wledFixture(), use_suggested_presets: true }} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /find wled devices/i }));
    const use = await screen.findByRole("button", { name: /^use$/i });
    fireEvent.click(use);
    const next = onChange.mock.calls.at(-1)?.[0];
    expect(next.device_address).toBe("10.0.0.9");
    expect(next.suggested_config.led_count).toBe(30);
  });
});
