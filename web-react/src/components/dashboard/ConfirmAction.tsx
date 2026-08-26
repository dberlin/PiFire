interface Props {
  open: boolean;
  /** Escape a positioned, overflow-clipped owner and cover the viewport. */
  viewport?: boolean;
  title: string;
  /** Optional body copy under the title — for consequences the title can't
      carry, e.g. a cascading delete. `.pf-modal-title` is a bold, centred
      20px headline, so a second sentence does not belong up there. */
  message?: string;
  /** Overrides the button wording when "Confirm"/"Cancel" would be vaguer than
      the two things actually on offer -- e.g. Restart Anyway / Restart Later,
      where "Cancel" reads like it cancels the update rather than deferring a
      restart. */
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm(): void;
  onCancel(): void;
}

export function ConfirmAction({
  open,
  viewport = false,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
}: Props) {
  if (!open) return null;
  return (
    <div className={`pf-modal-scrim${viewport ? " pf-modal-scrim-fixed" : ""}`} onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        {message && <div className="pf-modal-message">{message}</div>}
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button className="pf-modal-btn danger" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
