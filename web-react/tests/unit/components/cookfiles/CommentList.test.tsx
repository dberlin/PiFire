import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as actualCookfileApi from "../../../../src/helpers/files/cookfileApi" with {
  rstest: "importActual",
};
import type {
  CookFileAsset,
  CookFileComment,
} from "../../../../src/helpers/contracts/content.gen";

const addCookFileCommentMock = rs.fn();
const updateCookFileCommentMock = rs.fn();
const deleteCookFileCommentMock = rs.fn();
const setCommentAssetsMock = rs.fn();
rs.mock("../../../../src/helpers/files/cookfileApi", () => ({
  ...actualCookfileApi,
  addCookFileComment: (...args: unknown[]) => addCookFileCommentMock(...args),
  updateCookFileComment: (...args: unknown[]) => updateCookFileCommentMock(...args),
  deleteCookFileComment: (...args: unknown[]) => deleteCookFileCommentMock(...args),
  setCommentAssets: (...args: unknown[]) => setCommentAssetsMock(...args),
}));

const { CommentList } = await import("../../../../src/components/cookfiles/CommentList");

const ASSETS: CookFileAsset[] = [
  { id: "a1", filename: "a1.png", type: "png" },
  { id: "a2", filename: "a2.png", type: "png" },
  { id: "a3", filename: "a3.png", type: "png" },
];

function comment(overrides: Partial<CookFileComment> = {}): CookFileComment {
  return {
    id: "c1",
    text: "First light",
    date: "2026-07-20",
    time: "14:05",
    edited: "",
    assets: [],
    ...overrides,
  };
}

function mount(comments: CookFileComment[], onChanged = rs.fn(), assets = ASSETS) {
  return {
    onChanged,
    ...render(
      <CommentList
        filename="Sunday.pifire"
        parentId="parent-id"
        comments={comments}
        assets={assets}
        onChanged={onChanged}
      />,
    ),
  };
}

describe("CommentList", () => {
  beforeEach(() => {
    for (const mock of [
      addCookFileCommentMock,
      updateCookFileCommentMock,
      deleteCookFileCommentMock,
      setCommentAssetsMock,
    ]) {
      mock.mockReset();
      mock.mockResolvedValue(null);
    }
  });

  afterEach(cleanup);

  it("renders each comment's date, time and text", () => {
    mount([comment(), comment({ id: "c2", text: "Wrapped", time: "16:20" })]);
    expect(screen.getByText("First light")).toBeInTheDocument();
    expect(screen.getByText("Wrapped")).toBeInTheDocument();
    expect(screen.getByText("2026-07-20 14:05")).toBeInTheDocument();
  });

  it("shows an edited note only when `edited` is non-empty", () => {
    mount([comment(), comment({ id: "c2", edited: "2026-07-21 09:00" })]);
    expect(screen.getByText(/edited 2026-07-21 09:00/)).toBeInTheDocument();
    expect(screen.getAllByText(/edited/)).toHaveLength(1);
  });

  it("renders newlines as text, never as injected HTML", () => {
    //  The API deliberately does not do Flask's \n -> <br> substitution, so
    //  there is nothing to dangerouslySetInnerHTML and no XSS surface.
    const { container } = mount([comment({ text: "line one\n<script>alert(1)</script>" })]);
    const body = container.querySelector(".pf-cf-comment-text") as HTMLElement;
    expect(body.textContent).toBe("line one\n<script>alert(1)</script>");
    expect(container.querySelector("script")).toBeNull();
  });

  it("adding a comment posts the text, clears the box and refetches", async () => {
    const user = userEvent.setup();
    const onChanged = rs.fn();
    mount([], onChanged);

    await user.type(screen.getByLabelText("Add a comment"), "Bark forming");
    await user.click(screen.getByRole("button", { name: "Add comment" }));

    await waitFor(() =>
      expect(addCookFileCommentMock).toHaveBeenCalledWith("Sunday.pifire", "Bark forming"),
    );
    await waitFor(() => expect(screen.getByLabelText("Add a comment")).toHaveValue(""));
    expect(onChanged).toHaveBeenCalled();
  });

  it("editing shows a textarea seeded with the comment's text, and Save posts it", async () => {
    const user = userEvent.setup();
    mount([comment()]);

    await user.click(screen.getByRole("button", { name: "Edit comment from 2026-07-20 14:05" }));
    const editor = screen.getByLabelText("Edit comment from 2026-07-20 14:05");
    expect(editor).toHaveValue("First light");

    await user.clear(editor);
    await user.type(editor, "Second light");
    await user.click(screen.getByRole("button", { name: "Save comment" }));

    await waitFor(() =>
      expect(updateCookFileCommentMock).toHaveBeenCalledWith("Sunday.pifire", "c1", "Second light"),
    );
  });

  it("cancelling an edit restores the original text and posts nothing", async () => {
    const user = userEvent.setup();
    mount([comment()]);

    await user.click(screen.getByRole("button", { name: "Edit comment from 2026-07-20 14:05" }));
    await user.clear(screen.getByLabelText("Edit comment from 2026-07-20 14:05"));
    await user.type(screen.getByLabelText("Edit comment from 2026-07-20 14:05"), "throwaway");
    await user.click(screen.getByRole("button", { name: "Cancel edit" }));

    expect(updateCookFileCommentMock).not.toHaveBeenCalled();
    expect(screen.getByText("First light")).toBeInTheDocument();
  });

  it("a failed save keeps the editor open with the user's text intact", async () => {
    const user = userEvent.setup();
    updateCookFileCommentMock.mockRejectedValue(new Error("archive is read-only"));
    mount([comment()]);

    await user.click(screen.getByRole("button", { name: "Edit comment from 2026-07-20 14:05" }));
    await user.clear(screen.getByLabelText("Edit comment from 2026-07-20 14:05"));
    await user.type(screen.getByLabelText("Edit comment from 2026-07-20 14:05"), "Second light");
    await user.click(screen.getByRole("button", { name: "Save comment" }));

    expect(await screen.findByText("archive is read-only")).toBeInTheDocument();
    expect(screen.getByLabelText("Edit comment from 2026-07-20 14:05")).toHaveValue("Second light");
  });

  it("deleting a comment asks for confirmation first", async () => {
    const user = userEvent.setup();
    mount([comment()]);

    await user.click(screen.getByRole("button", { name: "Delete comment from 2026-07-20 14:05" }));
    expect(deleteCookFileCommentMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(deleteCookFileCommentMock).toHaveBeenCalledWith("Sunday.pifire", "c1"),
    );
  });

  it("dismissing the delete confirmation deletes nothing", async () => {
    const user = userEvent.setup();
    mount([comment()]);

    await user.click(screen.getByRole("button", { name: "Delete comment from 2026-07-20 14:05" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteCookFileCommentMock).not.toHaveBeenCalled();
  });

  it("attached assets render as thumbnails from the cook's asset folder", () => {
    mount([comment({ assets: ["a1.png", "a2.png"] })]);
    expect(screen.getByAltText("Attachment a1.png")).toHaveAttribute(
      "src",
      "/static/img/tmp/parent-id/thumbs/a1.png",
    );
  });

  it("the attach picker preselects the comment's current assets", async () => {
    const user = userEvent.setup();
    mount([comment({ assets: ["a2.png"] })]);

    await user.click(
      screen.getByRole("button", { name: "Attach media to comment from 2026-07-20 14:05" }),
    );
    expect(screen.getByLabelText("Attach a2.png")).toBeChecked();
    expect(screen.getByLabelText("Attach a1.png")).not.toBeChecked();
  });

  it("saving the picker posts the WHOLE resulting list, not a toggle", async () => {
    const user = userEvent.setup();
    mount([comment({ assets: ["a2.png"] })]);

    await user.click(
      screen.getByRole("button", { name: "Attach media to comment from 2026-07-20 14:05" }),
    );
    await user.click(screen.getByLabelText("Attach a1.png"));
    await user.click(screen.getByLabelText("Attach a2.png"));
    await user.click(screen.getByRole("button", { name: "Save attachments" }));

    //  A per-asset toggle inverts whenever the client's view of "selected" is
    //  stale; a whole-list write states the intent and cannot invert.
    await waitFor(() =>
      expect(setCommentAssetsMock).toHaveBeenCalledWith("Sunday.pifire", "c1", ["a1.png"]),
    );
  });

  it("the lightbox steps prev/next within the comment's assets and wraps", async () => {
    const user = userEvent.setup();
    mount([comment({ assets: ["a1.png", "a2.png"] })]);

    await user.click(screen.getByAltText("Attachment a1.png"));
    const dialog = screen.getByAltText("a1.png").closest(".pf-modal") as HTMLElement;
    expect(dialog).not.toBeNull();

    await user.click(within(dialog).getByRole("button", { name: "Next photo" }));
    expect(screen.getByAltText("a2.png")).toBeInTheDocument();

    //  Wraps within THIS comment's list, not across every asset in the file.
    await user.click(within(dialog).getByRole("button", { name: "Next photo" }));
    expect(screen.getByAltText("a1.png")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Previous photo" }));
    expect(screen.getByAltText("a2.png")).toBeInTheDocument();
  });

  it("says so when a cook has no comments", () => {
    mount([]);
    expect(screen.getByText(/No comments on this cook yet/)).toBeInTheDocument();
  });
});
