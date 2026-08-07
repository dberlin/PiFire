import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { screen, waitFor } from "@testing-library/react";
import * as actualSettingsApi from "../../../../src/helpers/settings/settingsApi" with {
  rstest: "importActual",
};
import { renderWithQuery } from "../../test-utils";

const getSettingsMock = rs.fn();
rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  ...actualSettingsApi,
  getSettings: (...a: unknown[]) => getSettingsMock(...a),
}));

const { useSettings } = await import("../../../../src/helpers/settings/useSettings");

function Probe() {
  const { data } = useSettings();
  return <div>{data?.globals?.grill_name ?? "pending"}</div>;
}

beforeEach(() => getSettingsMock.mockReset());

describe("useSettings", () => {
  it("exposes the settings blob once the read lands", async () => {
    getSettingsMock.mockResolvedValue({ globals: { grill_name: "Smokey" } });
    renderWithQuery(<Probe />);
    await waitFor(() => expect(screen.getByText("Smokey")).toBeVisible());
  });

  it("serves two mounted readers from ONE request", async () => {
    getSettingsMock.mockResolvedValue({ globals: { grill_name: "Smokey" } });
    renderWithQuery(
      <>
        <Probe />
        <Probe />
      </>,
    );
    await waitFor(() => expect(screen.getAllByText("Smokey")).toHaveLength(2));
    expect(getSettingsMock).toHaveBeenCalledTimes(1);
  });
});
