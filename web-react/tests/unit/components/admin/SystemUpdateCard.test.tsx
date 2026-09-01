import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { SystemUpdateCard } from "../../../../src/components/admin/SystemUpdateCard";
import * as api from "../../../../src/helpers/update/updateApi";

rs.mock("../../../../src/helpers/update/updateApi", () => ({ fetchUpdateCheck: rs.fn() }));
afterEach(cleanup);

describe("SystemUpdateCard", () => {
  it("shows the version and behind-count with a link to /update", async () => {
    (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      message: "",
      data: { current: "v1.8.0", behind: 2 },
    });
    render(
      <MemoryRouter>
        <SystemUpdateCard />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/v1\.8\.0/)).toBeInTheDocument();
    expect(await screen.findByText(/2 commits behind/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /updater/i })).toHaveAttribute("href", "/update");
  });
});
