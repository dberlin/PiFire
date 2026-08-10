import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  CookFileLabels,
  CookFileMetadata,
} from "../../../../src/helpers/contracts/content.gen";
import { FileRequestError } from "../../../../src/helpers/files/apiEnvelope";
// The URL builders and CookFileRequestError stay REAL so the download hrefs and
// the 409 branch are asserted against the shipped contract, not a stub of it.
import * as actualCookfileApi from "../../../../src/helpers/files/cookfileApi" with {
  rstest: "importActual",
};

const setCookFileTitleMock = rs.fn();
const renameCookFileLabelMock = rs.fn();
rs.mock("../../../../src/helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  setCookFileTitle: (...args: unknown[]) => setCookFileTitleMock(...args),
  renameCookFileLabel: (...args: unknown[]) => renameCookFileLabelMock(...args),
}));

const { CookFileMeta } = await import("../../../../src/components/cookfiles/CookFileMeta");

const METADATA: CookFileMetadata = {
  title: "Sunday Brisket",
  units: "F",
  thumbnail: "",
  id: "parent-id",
  version: "1.5.0",
  starttime: "12:00:00",
  endtime: "18:30:00",
  starttime_epoch: 1784942370612,
  endtime_epoch: 1784965970612,
};

const LABELS: CookFileLabels = {
  probes: { grill1: "Grill", probe1: "Probe 1" },
  targets: {},
  primarysp: {},
};

function mount(overrides: Partial<CookFileMetadata> = {}, onChanged = rs.fn()) {
  return {
    onChanged,
    ...render(
      <CookFileMeta
        filename="Sunday.pifire"
        metadata={{ ...METADATA, ...overrides }}
        labels={LABELS}
        onChanged={onChanged}
      />,
    ),
  };
}

describe("CookFileMeta", () => {
  beforeEach(() => {
    setCookFileTitleMock.mockReset();
    renameCookFileLabelMock.mockReset();
    setCookFileTitleMock.mockResolvedValue(null);
    renameCookFileLabelMock.mockResolvedValue({ new_label_safe: "MainGrill" });
  });

  afterEach(cleanup);

  it("shows title, filename, units, start and end time", () => {
    mount();
    expect(screen.getByLabelText("Title")).toHaveValue("Sunday Brisket");
    expect(screen.getByText("Sunday.pifire")).toBeInTheDocument();
    expect(screen.getByText("F")).toBeInTheDocument();
    expect(screen.getByText("12:00:00")).toBeInTheDocument();
    expect(screen.getByText("18:30:00")).toBeInTheDocument();
  });

  it("editing the title calls setCookFileTitle and asks the page to refetch", async () => {
    const user = userEvent.setup();
    const onChanged = rs.fn();
    mount({}, onChanged);

    await user.clear(screen.getByLabelText("Title"));
    await user.type(screen.getByLabelText("Title"), "Monday Pork");
    await user.click(screen.getByRole("button", { name: "Save title" }));

    await waitFor(() =>
      expect(setCookFileTitleMock).toHaveBeenCalledWith("Sunday.pifire", "Monday Pork"),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("a failed title save surfaces the error and restores the stored value", async () => {
    const user = userEvent.setup();
    setCookFileTitleMock.mockRejectedValue(new Error("disk full"));
    mount();

    await user.clear(screen.getByLabelText("Title"));
    await user.type(screen.getByLabelText("Title"), "Never Saved");
    await user.click(screen.getByRole("button", { name: "Save title" }));

    expect(await screen.findByText("disk full")).toBeInTheDocument();
    //  Leaving the failed draft in the box would tell the user their edit
    //  landed when it did not.
    await waitFor(() => expect(screen.getByLabelText("Title")).toHaveValue("Sunday Brisket"));
  });

  it("renders one rename row per probe, seeded with the current name", () => {
    mount();
    expect(screen.getByLabelText("New name for grill1")).toHaveValue("Grill");
    expect(screen.getByLabelText("New name for probe1")).toHaveValue("Probe 1");
  });

  it("saving a label sends the probe KEY as old_label, not the display name", async () => {
    const user = userEvent.setup();
    const onChanged = rs.fn();
    mount({}, onChanged);

    await user.clear(screen.getByLabelText("New name for grill1"));
    await user.type(screen.getByLabelText("New name for grill1"), "Main Grill");
    await user.click(screen.getByRole("button", { name: "Rename grill1" }));

    await waitFor(() =>
      expect(renameCookFileLabelMock).toHaveBeenCalledWith("Sunday.pifire", "grill1", "Main Grill"),
    );
    //  The row is re-keyed by the parent's refetch, never by this component
    //  guessing the server's create_safe_name transform.
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("a 409 says the label already exists, distinct from a generic failure", async () => {
    const user = userEvent.setup();
    renameCookFileLabelMock.mockRejectedValue(
      new FileRequestError({ status: 409, message: "label_exists", errortype: null }),
    );
    mount();

    await user.clear(screen.getByLabelText("New name for grill1"));
    await user.type(screen.getByLabelText("New name for grill1"), "Probe 1");
    await user.click(screen.getByRole("button", { name: "Rename grill1" }));

    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });

  it("a non-409 rename failure reports the server message", async () => {
    const user = userEvent.setup();
    renameCookFileLabelMock.mockRejectedValue(
      new FileRequestError({ status: 422, message: "Error: Unspecified", errortype: "other" }),
    );
    mount();

    await user.clear(screen.getByLabelText("New name for grill1"));
    await user.type(screen.getByLabelText("New name for grill1"), "Whatever");
    await user.click(screen.getByRole("button", { name: "Rename grill1" }));

    expect(await screen.findByText("Error: Unspecified")).toBeInTheDocument();
  });

  it("falls back to the placeholder when the archive has no thumbnail", () => {
    const { container } = mount();
    expect(container.querySelector("img.pf-cf-meta-thumb")).toHaveAttribute(
      "src",
      "/static/img/pifire-cf-thumb.png",
    );
  });

  it("points at the archive's own thumbnail when it has one", () => {
    const { container } = mount({ thumbnail: "shot.png" });
    expect(container.querySelector("img.pf-cf-meta-thumb")).toHaveAttribute(
      "src",
      "/static/img/tmp/parent-id/thumbs/shot.png",
    );
  });

  it("offers the archive and both CSVs as download links", () => {
    mount();
    expect(screen.getByRole("link", { name: "Download cook file" })).toHaveAttribute(
      "href",
      "/api/files/cookfiles/download?file=Sunday.pifire",
    );
    expect(screen.getByRole("link", { name: "Download raw CSV" })).toHaveAttribute(
      "href",
      "/api/files/cookfiles/export?file=Sunday.pifire&kind=data",
    );
    expect(screen.getByRole("link", { name: "Download events CSV" })).toHaveAttribute(
      "href",
      "/api/files/cookfiles/export?file=Sunday.pifire&kind=events",
    );
  });
});
