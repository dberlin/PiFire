import { THEME } from "../src/theme";

it("carries the three PiFire accents", () => {
  expect(Object.keys(THEME)).toEqual(["ember", "ice", "crimson"]);
});

it("gives every accent a full token set", () => {
  for (const tokens of Object.values(THEME)) {
    expect(tokens).toEqual(
      expect.objectContaining({
        accent: expect.stringMatching(/^#/),
        background: expect.stringMatching(/^#/),
        surface: expect.stringMatching(/^#/),
        text: expect.stringMatching(/^#/),
        danger: expect.stringMatching(/^#/),
      }),
    );
  }
});
