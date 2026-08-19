import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { createQueryClient } from "../../../../src/helpers/query/queryClient";
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

function Probe({ baseUrl, label }: { baseUrl?: string; label?: string }) {
  const { data } = useSettings(baseUrl);
  return (
    <div>
      {label}
      {data?.globals?.grill_name ?? "pending"}
    </div>
  );
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

  it("owns distinct normalized cache entries for API bases A and B", async () => {
    const client = createQueryClient();
    getSettingsMock.mockImplementation(async (baseUrl: string) => ({
      globals: { grill_name: baseUrl === "/a" ? "Grill A" : "Grill B" },
    }));

    render(
      <QueryClientProvider client={client}>
        <Probe baseUrl="/a/" label="A: " />
        <Probe baseUrl="/b/" label="B: " />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("A: Grill A")).toBeVisible();
      expect(screen.getByText("B: Grill B")).toBeVisible();
    });
    expect(getSettingsMock).toHaveBeenCalledTimes(2);
    expect(getSettingsMock.mock.calls.map(([baseUrl]) => baseUrl)).toEqual(
      expect.arrayContaining(["/a", "/b"]),
    );
    expect(client.getQueryData(queryKeys.settings("/a"))).toEqual({
      globals: { grill_name: "Grill A" },
    });
    expect(client.getQueryData(queryKeys.settings("/b"))).toEqual({
      globals: { grill_name: "Grill B" },
    });
  });

  it("invalidates settings for one normalized API base without invalidating another", async () => {
    const client = createQueryClient();
    getSettingsMock.mockImplementation(async (baseUrl: string) => ({
      globals: { grill_name: baseUrl === "/a" ? "Grill A" : "Grill B" },
    }));
    render(
      <QueryClientProvider client={client}>
        <Probe baseUrl="/a/" label="A: " />
        <Probe baseUrl="/b" label="B: " />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("B: Grill B")).toBeVisible());

    await client.invalidateQueries({
      queryKey: queryKeys.settingsRoot("/a/"),
      refetchType: "none",
    });

    expect(client.getQueryState(queryKeys.settings("/a"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.settings("/b"))?.isInvalidated).toBe(false);
  });
});
