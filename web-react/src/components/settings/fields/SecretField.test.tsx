import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { SecretField } from "./SecretField";

describe("SecretField", () => {
  // The whole point of the component: the value must not be readable off the
  // screen until the user asks for it. A masked input has no `textbox` role, so
  // the only way to reach it is by its label -- which is also the assertion
  // that the label is still correctly associated despite the toggle button
  // sitting between the label and the input.
  it("starts masked", () => {
    render(<SecretField label="InfluxDB Token" value="s3cret" onChange={rs.fn()} />);
    const input = screen.getByLabelText("InfluxDB Token");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue("s3cret");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("reveals the value on Show and re-masks it on Hide", () => {
    render(<SecretField label="InfluxDB Token" value="s3cret" onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Show InfluxDB Token" }));
    expect(screen.getByLabelText("InfluxDB Token")).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Hide InfluxDB Token" }));
    expect(screen.getByLabelText("InfluxDB Token")).toHaveAttribute("type", "password");
  });

  // The accessible name names the field, so a screen-reader user with six of
  // these on one page can tell which secret each button reveals; the visible
  // text is a prefix of that name, which is what WCAG 2.5.3 (Label in Name)
  // asks for.
  it("names the toggle after the field it reveals", () => {
    render(<SecretField label="MQTT Password" value="" onChange={rs.fn()} />);
    const toggle = screen.getByRole("button", { name: "Show MQTT Password" });
    expect(toggle).toHaveTextContent("Show");
  });

  it("calls onChange while masked", () => {
    const onChange = rs.fn();
    render(<SecretField label="Pushover API Key" value="" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Pushover API Key"), { target: { value: "po-key" } });
    expect(onChange).toHaveBeenCalledWith("po-key");
  });

  it("keeps editing working after a reveal", () => {
    const onChange = rs.fn();
    render(<SecretField label="Pushover API Key" value="" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show Pushover API Key" }));
    fireEvent.change(screen.getByLabelText("Pushover API Key"), { target: { value: "revealed" } });
    expect(onChange).toHaveBeenCalledWith("revealed");
  });

  // Two on one page must not share an id, or clicking either label focuses the
  // first one's input and getByLabelText becomes ambiguous.
  it("gives each field its own input id", () => {
    render(
      <>
        <SecretField label="IFTTT API Key" value="a" onChange={rs.fn()} />
        <SecretField label="MQTT Password" value="b" onChange={rs.fn()} />
      </>,
    );
    const first = screen.getByLabelText("IFTTT API Key");
    const second = screen.getByLabelText("MQTT Password");
    expect(first.id).not.toEqual("");
    expect(first.id).not.toEqual(second.id);
  });

  // A revealed secret must not survive a trip to another settings tab and back.
  // Remounting is what a tab switch does, so the reveal state living in local
  // state (not a draft) is the behaviour being pinned.
  it("re-masks on remount", () => {
    const { unmount } = render(
      <SecretField label="MQTT Password" value="hunter2" onChange={rs.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show MQTT Password" }));
    expect(screen.getByLabelText("MQTT Password")).toHaveAttribute("type", "text");
    unmount();

    render(<SecretField label="MQTT Password" value="hunter2" onChange={rs.fn()} />);
    expect(screen.getByLabelText("MQTT Password")).toHaveAttribute("type", "password");
  });

  // Browsers offer to save anything that looks like a login, and a password
  // manager filling the grill's InfluxDB token with the user's website password
  // is worse than no help at all.
  it("opts out of autofill and spellcheck", () => {
    render(<SecretField label="IFTTT API Key" value="" onChange={rs.fn()} />);
    const input = screen.getByLabelText("IFTTT API Key");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input).toHaveAttribute("spellcheck", "false");
  });
});
