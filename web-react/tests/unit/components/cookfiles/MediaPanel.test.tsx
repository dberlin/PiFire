import type { CookFileAsset } from "@pifire/core/contracts/content";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as actualCookfileApi from "../../../../src/helpers/files/cookfileApi" with {
  rstest: "importActual",
};

const uploadCookFileAssetsMock = rs.fn();
const deleteCookFileAssetsMock = rs.fn();
const setCookFileThumbnailMock = rs.fn();
rs.mock("../../../../src/helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  uploadCookFileAssets: (...args: unknown[]) => uploadCookFileAssetsMock(...args),
  deleteCookFileAssets: (...args: unknown[]) => deleteCookFileAssetsMock(...args),
  setCookFileThumbnail: (...args: unknown[]) => setCookFileThumbnailMock(...args),
}));

const { MediaPanel } = await import("../../../../src/components/cookfiles/MediaPanel");

const ASSETS: CookFileAsset[] = [
  { id: "a1", filename: "a1.png", type: "png" },
  { id: "a2", filename: "a2.png", type: "png" },
];

function mount(assets = ASSETS, thumbnail = "", onChanged = rs.fn()) {
  return {
    onChanged,
    ...render(
      <MediaPanel
        filename="Sunday.pifire"
        parentId="parent-id"
        assets={assets}
        thumbnail={thumbnail}
        onChanged={onChanged}
      />,
    ),
  };
}

function png(name: string) {
  return new File([new Uint8Array([1])], name, { type: "image/png" });
}

describe("MediaPanel", () => {
  beforeEach(() => {
    uploadCookFileAssetsMock.mockReset();
    deleteCookFileAssetsMock.mockReset();
    setCookFileThumbnailMock.mockReset();
    uploadCookFileAssetsMock.mockResolvedValue([]);
    deleteCookFileAssetsMock.mockResolvedValue(null);
    setCookFileThumbnailMock.mockResolvedValue(null);
  });

  afterEach(cleanup);

  it("renders a thumbnail grid over every asset, linking to the fullsize image", () => {
    mount();
    expect(screen.getByAltText("a1.png")).toHaveAttribute(
      "src",
      "/static/img/tmp/parent-id/thumbs/a1.png",
    );
    expect(screen.getByAltText("a1.png").closest("a")).toHaveAttribute(
      "href",
      "/static/img/tmp/parent-id/a1.png",
    );
  });

  it("uploading images posts them and refetches", async () => {
    const user = userEvent.setup();
    const onChanged = rs.fn();
    mount(ASSETS, "", onChanged);

    const image = png("shot.png");
    await user.upload(screen.getByLabelText("Upload photos"), image);

    await waitFor(() =>
      expect(uploadCookFileAssetsMock).toHaveBeenCalledWith("Sunday.pifire", [image]),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("sends multiple files in one request", async () => {
    const user = userEvent.setup();
    mount();

    const images = [png("one.png"), png("two.png")];
    await user.upload(screen.getByLabelText("Upload photos"), images);

    await waitFor(() => expect(uploadCookFileAssetsMock).toHaveBeenCalledTimes(1));
    expect(uploadCookFileAssetsMock.mock.calls[0][1]).toHaveLength(2);
  });

  it("an upload rejected as disallowed_file shows the reason", async () => {
    const user = userEvent.setup({ applyAccept: false });
    uploadCookFileAssetsMock.mockRejectedValue(new Error("disallowed_file"));
    mount();

    await user.upload(
      screen.getByLabelText("Upload photos"),
      new File([new Uint8Array([1])], "evil.svg", { type: "image/svg+xml" }),
    );
    expect(await screen.findByText("disallowed_file")).toBeInTheDocument();
  });

  it("selecting assets and confirming deletes exactly those", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByLabelText("Select a2.png"));
    await user.click(screen.getByRole("button", { name: "Remove selected photos (1)" }));
    expect(deleteCookFileAssetsMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(deleteCookFileAssetsMock).toHaveBeenCalledWith("Sunday.pifire", ["a2.png"]),
    );
  });

  it("the delete confirmation names the count", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByLabelText("Select a1.png"));
    await user.click(screen.getByLabelText("Select a2.png"));
    await user.click(screen.getByRole("button", { name: "Remove selected photos (2)" }));

    expect(screen.getByText("Remove 2 photos?")).toBeInTheDocument();
  });

  it("dismissing the delete confirmation removes nothing", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByLabelText("Select a1.png"));
    await user.click(screen.getByRole("button", { name: "Remove selected photos (1)" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteCookFileAssetsMock).not.toHaveBeenCalled();
  });

  it("choosing a thumbnail posts that asset", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Use a2.png as thumbnail" }));
    await waitFor(() =>
      expect(setCookFileThumbnailMock).toHaveBeenCalledWith("Sunday.pifire", "a2.png"),
    );
  });

  it("indicates the current thumbnail and does not offer to re-pick it", () => {
    mount(ASSETS, "a1.png");
    expect(screen.getByRole("button", { name: "Current thumbnail" })).toBeDisabled();
    expect(screen.getByAltText("a1.png").className).toContain("pf-cf-media-img--selected");
    expect(screen.getByAltText("a2.png").className).not.toContain("pf-cf-media-img--selected");
  });

  it("the empty state invites an upload instead of showing an empty grid", () => {
    const { container } = mount([]);
    expect(screen.getByText(/upload one to illustrate this cook/)).toBeInTheDocument();
    expect(container.querySelector(".pf-cf-media-grid")).toBeNull();
    expect(screen.getByLabelText("Upload photos")).toBeInTheDocument();
  });
});
