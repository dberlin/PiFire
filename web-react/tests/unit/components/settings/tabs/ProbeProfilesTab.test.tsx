import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { AppPrefsProvider } from "../../../../../src/components/AppPrefs";
import { HydrateFallback } from "../../../../../src/components/HydrateFallback";
import { SettingsShell } from "../../../../../src/components/settings/SettingsShell";
import { ProbeProfilesTab } from "../../../../../src/components/settings/tabs/ProbeProfilesTab";
import { testQueryClient } from "../../../test-utils";

const settings = {
  globals: { units: "F" },
  platform: { dc_fan: false },
  probe_settings: { probe_profiles: {}, probe_map: { probe_info: [], probe_devices: [] } },
};

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
});

it("keeps a pending profile save owned across tab remounts and unlocks its draft on refusal", async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  rs.stubGlobal(
    "fetch",
    rs.fn((url: string) =>
      url.endsWith("/api/probe_profiles")
        ? pending
        : Promise.resolve(new Response(JSON.stringify({ settings }), { status: 200 })),
    ),
  );
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        element: <SettingsShell />,
        loader: () => ({ settings: structuredClone(settings), mode: "Stop", controllerMeta: null }),
        HydrateFallback,
        children: [
          { path: "probe-profiles", element: <ProbeProfilesTab /> },
          { path: "general", element: <p>Another settings tab</p> },
        ],
      },
    ],
    { initialEntries: ["/settings/probe-profiles"] },
  );
  render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Add profile" }));
  for (const [label, value] of [
    ["Name", "Pending profile"],
    ["A", "0.0007"],
    ["B", "0.0002"],
    ["C", "9.5e-8"],
  ]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await act(async () => {
    await router.navigate("/settings/general");
  });
  await act(async () => {
    await router.navigate("/settings/probe-profiles");
  });
  expect(await screen.findByLabelText("Name")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Discard changes" })).toBeDisabled();
  await act(async () => {
    finish(
      new Response(
        JSON.stringify({ result: "Error", message: "bad_request", data: { field: "c" } }),
        { status: 400 },
      ),
    );
  });
  await waitFor(() => expect(screen.getByLabelText("Name")).toBeEnabled());
  expect(screen.getByLabelText("Name")).toHaveValue("Pending profile");
  expect(screen.getByLabelText("C")).toHaveValue("9.5e-8");
  expect(screen.getByRole("alert")).toHaveTextContent("Check c");
});
