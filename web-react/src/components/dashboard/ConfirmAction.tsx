interface Props {
  open: boolean;
  title: string;
  onConfirm(): void;
  onCancel(): void;
}

export function ConfirmAction({ open, title, onConfirm, onCancel }: Props) {
  if (!open) return null;
  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
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
