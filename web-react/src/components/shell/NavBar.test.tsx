import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { NavBar } from "./NavBar";

function renderNav(at = "/", grillName = "") {
  const router = createMemoryRouter([{ path: "*", element: <NavBar grillName={grillName} /> }], {
    initialEntries: [at],
  });
  return render(<RouterProvider router={router} />);
}

describe("NavBar", () => {
  it("renders all six navigation labels from base.html", () => {
    renderNav();
    for (const label of ["Dashboard", "Recipes", "History", "Events", "Settings", "Admin"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("renders the three ported destinations as real links", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Dashboard" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "History" }).getAttribute("href")).toBe("/history");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
  });

  it("renders the three unported destinations as disabled non-links", () => {
    renderNav();
    for (const label of ["Recipes", "Events", "Admin"]) {
      const el = screen.getByText(label);
      expect(el.tagName).not.toBe("A");
      expect(el.getAttribute("aria-disabled")).toBe("true");
      expect(screen.queryByRole("link", { name: label })).toBeNull();
    }
  });

  it("marks the dashboard item active at /", () => {
    renderNav("/");
    expect(screen.getByRole("link", { name: "Dashboard" }).className).toContain("active");
    expect(screen.getByRole("link", { name: "History" }).className).not.toContain("active");
  });

  it("marks the history item active at /history", () => {
    renderNav("/history");
    expect(screen.getByRole("link", { name: "History" }).className).toContain("active");
    expect(screen.getByRole("link", { name: "Dashboard" }).className).not.toContain("active");
  });

  it("marks settings active on a nested settings route", () => {
    renderNav("/settings/history");
    expect(screen.getByRole("link", { name: "Settings" }).className).toContain("active");
  });

  it("does not mark the dashboard active on a non-root route", () => {
    renderNav("/settings");
    expect(screen.getByRole("link", { name: "Dashboard" }).className).not.toContain("active");
  });

  it("renders the brand as a link home", () => {
    renderNav();
    const brand = screen.getByRole("link", { name: /PiFire/ });
    expect(brand.getAttribute("href")).toBe("/");
  });

  it("renders the grill name when one is provided", () => {
    renderNav("/", "Backyard Smoker");
    expect(screen.getByText("Backyard Smoker")).toBeTruthy();
  });

  it("omits the grill name when it is empty", () => {
    const { container } = renderNav("/", "");
    expect(container.querySelector(".pf-nav-grill")).toBeNull();
  });

  it("collapses the menu on small screens and toggles it open", async () => {
    const user = userEvent.setup();
    const { container } = renderNav();
    const toggle = screen.getByRole("button", { name: /toggle navigation/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".pf-nav-list.open")).toBeNull();

    await user.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector(".pf-nav-list.open")).toBeTruthy();

    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes the collapsed menu after following a link", async () => {
    const user = userEvent.setup();
    const { container } = renderNav();
    await user.click(screen.getByRole("button", { name: /toggle navigation/i }));
    expect(container.querySelector(".pf-nav-list.open")).toBeTruthy();

    await user.click(screen.getByRole("link", { name: "History" }));

    expect(container.querySelector(".pf-nav-list.open")).toBeNull();
  });

  it("the toggle button controls the element it names", () => {
    const { container } = renderNav();
    const toggle = screen.getByRole("button", { name: /toggle navigation/i });
    const controls = toggle.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    expect(container.querySelector(`#${controls}`)).toBeTruthy();
  });
});
