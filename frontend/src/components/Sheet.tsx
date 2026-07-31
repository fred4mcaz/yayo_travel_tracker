import { useEffect, useRef } from "react";

interface Props {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

/** Modal on desktop, bottom sheet on mobile (CSS decides which). */
export function Sheet({ title, onClose, children, footer }: Props) {
  const panel = useRef<HTMLDivElement>(null);

  // Callers pass an inline arrow for onClose, so its identity changes on every
  // render. Depending on it here re-ran this effect on every keystroke, and the
  // autofocus below then yanked the caret back to the first field -- which is
  // why you could only type one character at a time. Hold it in a ref so the
  // effect can run exactly once, on mount, while the handler stays current.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCloseRef.current();
    }
    document.addEventListener("keydown", onKey);
    // Stop the page behind from scrolling while the sheet is open.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.querySelector<HTMLElement>("input, select, textarea")?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, []);

  return (
    <div
      className="sheet-backdrop"
      onMouseDown={(e) => {
        // Only dismiss on a click that both starts and ends on the backdrop,
        // so dragging to select text inside the sheet cannot close it.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="sheet"
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="sheet-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="sheet-body">{children}</div>
        {footer && <footer className="sheet-foot">{footer}</footer>}
      </div>
    </div>
  );
}
