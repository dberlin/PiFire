import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { learningPillStatus, learningStatusLabel, learningStatusTone } from "./learningDisplay";

const FOCUS_RING =
  "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent";
const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

export interface LearningDialogProps {
  controllerLabel: string;
  title: string;
  closeLabel: string;
  status: string;
  currentMode: string;
  displayMode: string;
  criticalError: boolean;
  loading: boolean;
  loadingLabel: string;
  error: string | null;
  retryLabel: string;
  onRetry(): void | Promise<void>;
  children: ReactNode;
}

export function LearningDialog({
  controllerLabel,
  title,
  closeLabel,
  status,
  currentMode,
  displayMode,
  criticalError,
  loading,
  loadingLabel,
  error,
  retryLabel,
  onRetry,
  children,
}: LearningDialogProps) {
  const [open, setOpen] = useState(false);
  const triggerButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);
  const titleId = useId();

  useEffect(() => {
    if (!open) {
      if (wasOpen.current) triggerButton.current?.focus();
      wasOpen.current = false;
      return;
    }

    wasOpen.current = true;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = dialog.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const focusIsInside =
        document.activeElement !== null &&
        dialog.current?.contains(document.activeElement) === true;

      if (!focusIsInside) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const pillStatus = learningPillStatus(status, currentMode, displayMode, criticalError);

  return (
    <>
      <button
        ref={triggerButton}
        className={`pf-btn pf-dash-learning ${FOCUS_RING}`}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-busy={loading || undefined}
        onClick={() => setOpen(true)}
      >
        {controllerLabel} learning: {pillStatus}
      </button>
      {open &&
        createPortal(
          <div className="pf-modal-scrim pf-modal-scrim-fixed" onClick={() => setOpen(false)}>
            <section
              ref={dialog}
              className="pf-modal max-h-full w-11/12 max-w-5xl min-w-0 overflow-y-auto text-text"
              role="dialog"
              aria-modal="true"
              aria-labelledby={titleId}
              aria-busy={loading || undefined}
              onClick={(event) => event.stopPropagation()}
            >
              <header className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h2 id={titleId} className="text-xl font-bold">
                    {title}
                  </h2>
                  <p
                    className={`mt-1 font-semibold ${error === null ? learningStatusTone(status) : "text-danger"}`}
                  >
                    {error === null ? learningStatusLabel(status) : "Error"}
                  </p>
                </div>
                <button
                  ref={closeButton}
                  className={`pf-toggle shrink-0 ${FOCUS_RING}`}
                  type="button"
                  aria-label={closeLabel}
                  onClick={() => setOpen(false)}
                >
                  Close
                </button>
              </header>

              {loading && <p role="status">{loadingLabel}</p>}
              {error !== null && (
                <div className="rounded-lg border border-danger p-3 text-danger" role="alert">
                  <p>{error}</p>
                  <button
                    className={`pf-modal-btn mt-2 ${FOCUS_RING}`}
                    type="button"
                    onClick={() => void onRetry()}
                  >
                    {retryLabel}
                  </button>
                </div>
              )}

              {children}
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
