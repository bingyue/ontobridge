import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
export default function StudioModal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null),
    onClose = useRef(close);
  onClose.current = close;
  useEffect(() => {
    const prior = document.activeElement as HTMLElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current
      ?.querySelector<HTMLElement>("input,select,textarea,button")
      ?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose.current();
      if (event.key === "Tab") {
        const fields = Array.from(
          ref.current?.querySelectorAll<HTMLElement>(
            'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
          ) || [],
        );
        const first = fields[0],
          last = fields.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      document.body.style.overflow = overflow;
      prior?.focus();
    };
  }, []);
  return (
    <div className="overlay" onClick={close}>
      <section
        ref={ref}
        className="modal wide"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">ONTOLOGY STUDIO</span>
            <h2>{title}</h2>
          </div>
          <button aria-label="关闭" onClick={close}>
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
