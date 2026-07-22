import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import { renderRoute } from "../../../test-utils";
import { GeneralTab } from "./GeneralTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

afterEach(cleanup);

// Harness for the render-phase resync case: keeps GeneralTab mounted while
// swapping the outlet context object out from under it, so we exercise the
// `settings !== prevSettings` branch on a live component instead of a fresh
// mount (a fresh renderRoute() call per settings object wouldn't touch that
// branch at all).
let setOutletContext: ((ctx: unknown) => void) | null = null;

function ContextHolder({ initial }: { initial: unknown }) {
  const [ctx, setCtx] = useState(initial);
  // Publishing the setter is a side effect (module-level mutable ref used
  // only by the test below to drive a re-render), so it belongs in an
  // effect, not directly in the render body.
  useEffect(() => {
    setOutletContext = setCtx;
  }, []);
  return <Outlet context={ctx} />;
}

function renderResyncHarness(initial: unknown) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<ContextHolder initial={initial} />}>
          <Route index element={<GeneralTab />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("GeneralTab", () => {
  it("renders grill name and theme fields with loaded values", () => {
    const context = {
      settings: {
        globals: {
          grill_name: "Backyard Smoker",
          page_theme: "dark",
        },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);

    expect(screen.getByDisplayValue("Backyard Smoker")).toBeInTheDocument();
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("dark");
  });

  it("saves the edited grill name with empty flags when Save is clicked", async () => {
    const context = {
      settings: {
        globals: {
          grill_name: "Old Name",
          page_theme: "light",
        },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);

    const nameInput = screen.getByDisplayValue("Old Name");
    fireEvent.change(nameInput, { target: { value: "New Name" } });

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      {
        globals: {
          grill_name: "New Name",
          page_theme: "light",
        },
      },
      [],
    );
  });

  it("resyncs displayed values when the settings object changes on re-render", () => {
    const first = {
      settings: {
        globals: {
          grill_name: "First Grill",
          page_theme: "light",
        },
      },
      mode: "Stop",
    };

    renderResyncHarness(first);
    expect(screen.getByDisplayValue("First Grill")).toBeInTheDocument();
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("light");

    const second = {
      settings: {
        globals: {
          grill_name: "Second Grill",
          page_theme: "dark",
        },
      },
      mode: "Stop",
    };

    act(() => {
      setOutletContext?.(second);
    });

    expect(screen.getByDisplayValue("Second Grill")).toBeInTheDocument();
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("dark");
  });
});
