interface Props {
  open: boolean;
  title: string;
  /** Optional body copy under the title — for consequences the title can't
      carry, e.g. a cascading delete. `.pf-modal-title` is a bold, centred
      20px headline, so a second sentence does not belong up there. */
  message?: string;
  onConfirm(): void;
  onCancel(): void;
}

export function ConfirmAction({ open, title, message, onConfirm, onCancel }: Props) {
  if (!open) return null;
  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        {message && <div className="pf-modal-message">{message}</div>}
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="pf-modal-btn danger" onClick={onConfirm}>
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}
