import { useEffect } from "react";

// Escape-to-dismiss for overlays.
//
// A scrim click is a mouse affordance; this is its keyboard equivalent, and
// every overlay needs one. Listening on `window` rather than the overlay
// element is deliberate: the overlay does not hold focus, so a local handler
// would only fire once something inside it happened to be focused.
//
// The listener is registered only while `active`, so a closed overlay cannot
// steal Escape from whatever sits underneath it.
export function useDismissOnEscape(active: boolean, onDismiss: () => void): void {
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, onDismiss]);
}
