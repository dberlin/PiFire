import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { RecipeView } from "../../../../src/components/recipes/RecipeView";
import type { RecipeDetail } from "../../../../src/helpers/files/recipeTypes";

const DETAIL: RecipeDetail = {
  filename: "brisket.pfrecipe",
  metadata: {
    author: "Alex",
    username: "alex",
    id: "recipe-id-1",
    title: "Sunday Brisket",
    description: "Low and slow.",
    image: "splash.png",
    thumbnail: "splash-thumb.png",
    units: "F",
    prep_time: 20,
    cook_time: 600,
    rating: 4,
    difficulty: "Hard",
    version: "1.0",
    food_probes: 2,
  },
  recipe: {
    ingredients: [{ name: "Brisket", quantity: "1 whole", assets: ["brisket.jpg"] }],
    instructions: [
      { text: "Trim the fat cap.", ingredients: ["Brisket"], assets: [], step: 0 },
      { text: "Hold until probe-tender.", ingredients: ["Brisket"], assets: [], step: 2 },
    ],
    steps: [
      {
        mode: "Startup",
        hold_temp: 0,
        timer: 0,
        notify: false,
        message: "",
        pause: false,
        trigger_temps: { primary: 0, food: [] },
      },
      // The disabled-sentinel case: hold_temp and trigger_temps.primary are both
      // 0 (unset), one food trigger is unset (0) and the other is set (150).
      {
        mode: "Hold",
        hold_temp: 0,
        timer: 30,
        notify: true,
        message: "Wrap it now",
        pause: true,
        trigger_temps: { primary: 0, food: [0, 150] },
      },
      {
        mode: "Shutdown",
        hold_temp: 0,
        timer: 0,
        notify: false,
        message: "",
        pause: false,
        trigger_temps: { primary: 0, food: [] },
      },
    ],
  },
  assets: [],
};

beforeEach(() => {
  Element.prototype.scrollIntoView = rs.fn();
});

describe("RecipeView", () => {
  it("renders 0 as an em-dash, never as the digit 0, for hold_temp and every trigger_temps member", () => {
    render(<RecipeView detail={DETAIL} activeStep={null} />);
    // Step 1 (index 1) is the Hold step under test.
    const stepBody = screen.getByText("Hold temp").closest("dl") as HTMLElement;

    const dashes = stepBody.querySelectorAll("dd");
    const values = [...dashes].map((dd) => dd.textContent);
    // Hold temp, primary trigger, and the first (unset) food trigger all read
    // as the sentinel dash; the second food trigger (150) reads as a real
    // temperature.
    expect(values).toContain("—");
    expect(values.filter((v) => v === "—").length).toBeGreaterThanOrEqual(3);
    expect(values).toContain("150°F");
  });

  it("labels timer as minutes rather than a bare number", () => {
    render(<RecipeView detail={DETAIL} activeStep={null} />);
    expect(screen.getByText("30 min")).toBeInTheDocument();
  });

  it("renders ingredient thumbnails from the recipe id, not the listing thumbnail path", () => {
    render(<RecipeView detail={DETAIL} activeStep={null} />);
    // At least one asset thumbnail must resolve under the recipe's own id.
    const thumb = screen
      .getAllByRole("img")
      .find((el) => el.getAttribute("src")?.includes("brisket.jpg"));
    expect(thumb?.getAttribute("src")).toBe("/static/img/tmp/recipe-id-1/brisket.jpg");
  });

  it("shows instructions' ingredient NAME strings and the Prep/Step program-step label", () => {
    render(<RecipeView detail={DETAIL} activeStep={null} />);
    expect(screen.getByText("Trim the fat cap.")).toBeInTheDocument();
    expect(screen.getByText("Prep")).toBeInTheDocument();
    // "Step 2" is ambiguous on its own -- the program-step list also has a
    // step numbered 2 -- so scope to the instructions table's own cell.
    const programStepCell = screen.getAllByText("Step 2").find((el) => el.tagName === "TD");
    expect(programStepCell).toBeTruthy();
    // "Brisket" appears as an ingredients-used entry for both instructions.
    expect(screen.getAllByText("Brisket").length).toBeGreaterThanOrEqual(2);
  });

  it("highlights the active step and scrolls it into view", () => {
    render(<RecipeView detail={DETAIL} activeStep={1} />);
    const active = document.querySelector(".pf-rcp-step--active");
    expect(active).not.toBeNull();
    expect(active?.textContent).toContain("Step 1");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("highlights no step when nothing is running", () => {
    render(<RecipeView detail={DETAIL} activeStep={null} />);
    expect(document.querySelector(".pf-rcp-step--active")).toBeNull();
  });
});
