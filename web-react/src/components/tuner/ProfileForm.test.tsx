import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as actualTunerApi from "../../helpers/tuner/tunerApi" with { rstest: "importActual" };

const saveProfileMock = rs.fn();
rs.mock("../../helpers/tuner/tunerApi", () => ({
  ...actualTunerApi,
  saveProfile: (...a: unknown[]) => saveProfileMock(...a),
}));

const { ProfileForm } = await import("./ProfileForm");

const COEFFICIENTS = { a: 0.0007343140544, b: 0.0002157437229, c: 0.0000000951568577 };

beforeEach(() => {
  saveProfileMock.mockReset();
  saveProfileMock.mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { id: "abc", applied: null },
  });
});

describe("ProfileForm", () => {
  it("shows the computed coefficients read-only", () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    for (const key of ["A", "B", "C"]) {
      expect(screen.getByRole("textbox", { name: key })).toHaveAttribute("readonly");
    }
  });

  it("requires a name before either save", () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    expect(screen.getByRole("button", { name: "Save & Apply" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save Only" })).toBeDisabled();
  });

  it("Save Only sends no probe label", async () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save Only" }));
    expect(saveProfileMock.mock.calls[0][0]).toEqual({
      ...COEFFICIENTS,
      name: "My Probe",
      apply_to: null,
    });
  });

  it("Save & Apply attaches it to the probe being tuned", async () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save & Apply" }));
    expect(saveProfileMock.mock.calls[0][0].apply_to).toBe("Grill");
  });

  it("reports the saved profile to its parent", async () => {
    const onSaved = rs.fn();
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={onSaved} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save Only" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ id: "abc", applied: null }));
  });

  it("renders a refusal in place and does not claim success", async () => {
    saveProfileMock.mockResolvedValue({
      ok: false,
      status: 404,
      message: "not_found",
      data: null,
    });
    const onSaved = rs.fn();
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={onSaved} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save & Apply" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer configured/i);
    expect(onSaved).not.toHaveBeenCalled();
  });
});
