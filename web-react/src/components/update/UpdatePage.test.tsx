import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import * as api from "../../helpers/update/updateApi";
import { UpdatePage } from "./UpdatePage";

rs.mock("../../helpers/update/updateApi", () => ({
  fetchUpdateState: rs.fn(),
  fetchUpdateCheck: rs.fn(),
  fetchUpdateLog: rs.fn(),
  fetchUpdateStatus: rs.fn(),
  refreshBranches: rs.fn(),
  changeBranch: rs.fn(),
  pullUpdate: rs.fn(),
  upgradeDeps: rs.fn(),
}));

const state = {
  ok: true,
  status: 200,
  message: "",
  data: {
    version: "v1.8.0",
    branch: "main",
    branches: ["main", "dev"],
    remote_url: "u",
    remote_version: "v1.8.1",
  },
};

function seed(overrides: Partial<Record<keyof typeof api, unknown>> = {}) {
  (api.fetchUpdateState as ReturnType<typeof rs.fn>).mockResolvedValue(state);
  (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { current: "v1.8.0", behind: 3 },
  });
  for (const [k, v] of Object.entries(overrides)) {
    (api[k as keyof typeof api] as ReturnType<typeof rs.fn>).mockResolvedValue(v);
  }
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <UpdatePage />
    </MemoryRouter>,
  );

afterEach(cleanup);

describe("UpdatePage", () => {
  it("shows the current version, branch and commits-behind", async () => {
    seed();
    renderPage();
    expect(await screen.findByText(/v1\.8\.0/)).toBeInTheDocument();
    expect(await screen.findByText(/3 commits behind/i)).toBeInTheDocument();
  });

  it("Change Branch posts the selected branch", async () => {
    seed({ changeBranch: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.change(screen.getByLabelText(/branch/i), { target: { value: "dev" } });
    fireEvent.click(screen.getByRole("button", { name: /change branch/i }));
    await waitFor(() => expect(api.changeBranch).toHaveBeenCalledWith("dev"));
  });

  it("Update to latest calls pullUpdate", async () => {
    seed({ pullUpdate: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    await waitFor(() => expect(api.pullUpdate).toHaveBeenCalled());
  });

  it("surfaces a 409 refusal from pullUpdate as an inline message", async () => {
    seed({ pullUpdate: { ok: false, status: 409, message: "system_active", data: null } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    expect(await screen.findByText(/stop the grill/i)).toBeInTheDocument();
  });

  it("Show log fetches and renders the git log", async () => {
    seed({
      fetchUpdateLog: { ok: true, status: 200, message: "", data: { output: "abc123 fix" } },
    });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /show log/i }));
    expect(await screen.findByText(/abc123 fix/)).toBeInTheDocument();
  });
});
