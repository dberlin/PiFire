import { useEffect } from "react";

export interface MenuItem {
  label: string;
  value: string;
}

interface Props {
  open: boolean;
  title: string;
  items: MenuItem[];
  onPick(value: string): void;
  onCancel(): void;
}

// A pick-one list, used wherever Flask has a dropup with more than a couple of
// choices (the Prime amounts, the P-Mode values). Built on the same
// .pf-modal-scrim / .pf-modal / .pf-modal-title / .pf-modal-btn vocabulary as
// the confirmation and setpoint modals -- deliberately not a second visual
// language, and not a native <select>, which is unusable on the 800x480
// touchscreen this design targets.
export function ActionMenu({ open, title, items, onPick, onCancel }: Props) {
  // Escape closes. This is a side effect on the document, not derived state, so
  // an effect is the right tool here.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        <div className="pf-menu-list">
          {items.map((item) => (
            <button
              key={item.value}
              className="pf-modal-btn pf-menu-item"
              onClick={() => onPick(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
