import { render } from "@testing-library/react-native";

import { HistoryChart } from "../src/components/HistoryChart";

it("renders one line per series", async () => {
  const { getAllByTestId } = await render(
    <HistoryChart
      series={[
        {
          label: "Grill",
          points: [
            [0, 200],
            [60, 225],
          ],
        },
        {
          label: "Brisket",
          points: [
            [0, 40],
            [60, 55],
          ],
        },
      ]}
    />,
  );
  expect(getAllByTestId("history-line")).toHaveLength(2);
});

it("says so when there is nothing to plot", async () => {
  const { getByText } = await render(<HistoryChart series={[]} />);
  expect(getByText(/no history/i)).toBeTruthy();
});

it("does not crash on a single point (zero-width time range)", async () => {
  const { getAllByTestId } = await render(
    <HistoryChart series={[{ label: "Grill", points: [[0, 200]] }]} />,
  );
  expect(getAllByTestId("history-line")).toHaveLength(1);
});

it("does not crash when every value is identical (zero-height range)", async () => {
  const { getAllByTestId } = await render(
    <HistoryChart
      series={[
        {
          label: "Grill",
          points: [
            [0, 200],
            [30, 200],
            [60, 200],
          ],
        },
      ]}
    />,
  );
  const lines = getAllByTestId("history-line");
  expect(lines).toHaveLength(1);
  const d = lines[0].props.d as string;
  expect(d).not.toMatch(/NaN/);
});

it("skips gaps (null readings) without crashing", async () => {
  const { getAllByTestId } = await render(
    <HistoryChart
      series={[
        {
          label: "Grill",
          points: [
            [0, 200],
            [30, null],
            [60, 210],
          ],
        },
      ]}
    />,
  );
  const lines = getAllByTestId("history-line");
  expect(lines).toHaveLength(1);
  expect(lines[0].props.d as string).not.toMatch(/NaN/);
});
