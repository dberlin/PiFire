import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigOptionField } from "../../../../src/components/wizard/ConfigOptionField";
import type { ConfigOption } from "../../../../src/helpers/contracts/wizard.gen";

afterEach(cleanup);

describe("ConfigOptionField", () => {
  const listOption: ConfigOption = {
    option_name: "units",
    option_friendly_name: "Units",
    option_type: "list",
    list_values: ["F", "C"],
    list_labels: ["Fahrenheit", "Celsius"],
  };

  const stringOption: ConfigOption = {
    option_name: "label",
    option_friendly_name: "Label",
    option_type: "string",
  };

  it("renders a labeled select with list options", () => {
    render(<ConfigOptionField option={listOption} value="F" onChange={rs.fn()} />);
    expect(screen.getByText("Units")).toBeInTheDocument();
    expect(screen.getByText("Fahrenheit")).toBeInTheDocument();
    expect(screen.getByText("Celsius")).toBeInTheDocument();
  });

  it("selects the option matching value via String() comparison", () => {
    render(<ConfigOptionField option={listOption} value="C" onChange={rs.fn()} />);
    expect(screen.getByRole("combobox")).toHaveValue("C");
  });

  it("emits the chosen list_values entry as a string on change", () => {
    const onChange = rs.fn();
    render(<ConfigOptionField option={listOption} value="F" onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "C" } });
    expect(onChange).toHaveBeenCalledWith("C");
  });

  it("renders a labeled text input for string options", () => {
    render(<ConfigOptionField option={stringOption} value="hello" onChange={rs.fn()} />);
    expect(screen.getByText("Label")).toBeInTheDocument();
    const input = screen.getByRole("textbox");
    expect(input).toHaveValue("hello");
  });

  it("emits typed value for string options", () => {
    const onChange = rs.fn();
    render(<ConfigOptionField option={stringOption} value="" onChange={onChange} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "world" } });
    expect(onChange).toHaveBeenCalledWith("world");
  });

  it("shows an empty string when value is undefined for string options", () => {
    render(<ConfigOptionField option={stringOption} value={undefined} onChange={rs.fn()} />);
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("falls back to the manifest default for a list option with no stored value", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "rotation",
          option_friendly_name: "Screen Rotation",
          option_type: "list",
          list_values: [0, 90, 180, 270],
          list_labels: ["0°", "90°", "180°", "270°"],
          default: 90,
        }}
        value={undefined}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Screen Rotation" })).toHaveValue("90");
  });

  it("falls back to the manifest default for a string option with no stored value", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "display_data_filename",
          option_friendly_name: "Layout File",
          option_type: "string",
          default: "./display/default.json",
        }}
        value={undefined}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Layout File" })).toHaveValue(
      "./display/default.json",
    );
  });

  it("prefers a stored value over the manifest default", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "rotation",
          option_friendly_name: "Screen Rotation",
          option_type: "list",
          list_values: [0, 90, 180, 270],
          list_labels: ["0°", "90°", "180°", "270°"],
          default: 90,
        }}
        value={270}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Screen Rotation" })).toHaveValue("270");
  });

  it("keeps a falsy-but-real stored value instead of the manifest default", () => {
    // Regression pin for the fallback's `=== undefined` check: 0 is a REAL
    // configured value. A naive `value || option.default` or truthiness test
    // would render "90" here instead of "0".
    render(
      <ConfigOptionField
        option={{
          option_name: "rotation",
          option_friendly_name: "Screen Rotation",
          option_type: "list",
          list_values: [0, 90, 180, 270],
          list_labels: ["0°", "90°", "180°", "270°"],
          default: 90,
        }}
        value={0}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Screen Rotation" })).toHaveValue("0");
  });

  it("keeps an empty-string stored value instead of the manifest default", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "display_data_filename",
          option_friendly_name: "Layout File",
          option_type: "string",
          default: "./display/default.json",
        }}
        value=""
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Layout File" })).toHaveValue("");
  });

  it("renders the manifest's description and wires it to the control via aria-describedby", () => {
    // Text and shape taken from wizard_manifest.json's ili9341e display config,
    // not invented -- a hand-written fixture only proves it agrees with itself.
    const rotation: ConfigOption = {
      option_name: "rotation",
      option_friendly_name: "Screen Rotation",
      option_description: "Select the display rotation angle (Usually 0 degrees).",
      option_type: "list",
      list_values: [0, 1, 2, 3],
      list_labels: ["0 Degrees", "90 Degrees", "180 Degrees", "270 Degrees"],
      default: 0,
    };
    render(<ConfigOptionField option={rotation} value={0} onChange={rs.fn()} />);
    const control = screen.getByRole("combobox", { name: "Screen Rotation" });
    const description = screen.getByText("Select the display rotation angle (Usually 0 degrees).");
    expect(control.getAttribute("aria-describedby")).toBe(description.id);
  });

  it("renders nothing when hidden", () => {
    const hiddenOption: ConfigOption = { ...listOption, hidden: true };
    const { container } = render(
      <ConfigOptionField option={hiddenOption} value="F" onChange={rs.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
