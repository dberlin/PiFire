import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";

import { NavBar } from "../../../../src/components/shell/NavBar";

interface TimerProps {
  visible?: boolean;
  running?: boolean;
  onToggle?: () => void;
}

function renderNav(at = "/", grillName = "", timer: TimerProps = {}) {
  const router = createMemoryRouter(
    [
      {
        path: "*",
        element: (
          <NavBar
            grillName={grillName}
            timerVisible={timer.visible ?? false}
            timerRunning={timer.running ?? false}
            onToggleTimer={timer.onToggle ?? (() => {})}
          />
        ),
      },
    ],
    { initialEntries: [at] },
  );
  return render(<RouterProvider router={router} />);
}

const stopwatch = () => screen.getByRole("button", { name: /toggle timer bar/i });

describe("NavBar", () => {
  it("renders all six navigation labels from base.html", () => {
    renderNav();
    for (const label of ["Dashboard", "Recipes", "History", "Events", "Settings", "Admin"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("renders the ported destinations as real links", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Dashboard" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "History" }).getAttribute("href")).toBe("/history");
    // Pellets has no base.html counterpart -- Flask reaches the pellet manager
    // from the dashboard hopper card, so this entry exists only in the new UI.
    expect(screen.getByRole("link", { name: "Pellets" }).getAttribute("href")).toBe("/pellets");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
    expect(screen.getByRole("link", { name: "Recipes" }).getAttribute("href")).toBe("/recipes");
    expect(screen.getByRole("link", { name: "Admin" }).getAttribute("href")).toBe("/admin");
    expect(screen.getByRole("link", { name: "Events" }).getAttribute("href")).toBe("/events");
  });

  it("renders every destination as a link, none disabled", () => {
    //  This replaces "renders the one remaining unported destination as a
    //  disabled non-link", which named Events. Events now has a route, so no
    //  entry is unported and the disabled span is gone from NavBar entirely.
    renderNav();
    for (const label of ["Dashboard", "Recipes", "History", "Pellets", "Events", "Settings"]) {
      const el = screen.getByText(label);
      expect(el.tagName).toBe("A");
      expect(el.getAttribute("aria-disabled")).toBeNull();
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

// base.html:50-57 -- the stopwatch that shows and hides the timer bar.
describe("NavBar timer toggle", () => {
  it("renders a stopwatch button", () => {
    renderNav();
    expect(stopwatch()).toBeTruthy();
  });

  it("reads as unpressed while the bar is hidden", () => {
    renderNav("/", "", { visible: false });
    expect(stopwatch().getAttribute("aria-pressed")).toBe("false");
    expect(stopwatch().className).not.toContain("on");
  });

  it("reads as pressed while the bar is showing", () => {
    renderNav("/", "", { visible: true });
    expect(stopwatch().getAttribute("aria-pressed")).toBe("true");
    expect(stopwatch().className).toContain("on");
  });

  it("asks the shell to toggle when clicked", async () => {
    const onToggle = rs.fn();
    const user = userEvent.setup();
    renderNav("/", "", { onToggle });

    await user.click(stopwatch());

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("marks itself while a timer is counting down (timer.js:207)", () => {
    renderNav("/", "", { running: true });
    expect(stopwatch().className).toContain("running");
  });

  it("carries no running marker when no timer is counting down", () => {
    renderNav("/", "", { running: false });
    expect(stopwatch().className).not.toContain("running");
  });

  it("stays outside the collapsible menu, as it does in base.html", () => {
    const { container } = renderNav();
    const list = container.querySelector("#pf-nav-list");
    expect(list).toBeTruthy();
    expect(list?.contains(stopwatch())).toBe(false);
  });

  it("does not answer to the navigation toggle's name", () => {
    renderNav();
    expect(screen.getByRole("button", { name: /toggle navigation/i })).not.toBe(stopwatch());
  });
});
