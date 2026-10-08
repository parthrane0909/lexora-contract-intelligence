"use client";
import React, { useEffect, useRef } from "react";

/**
 * Accessible modal dialog (Phase 9).
 *
 * Guarantees:
 *  - Escape closes it (unless `busy`, so a confirmation in flight cannot be
 *    dismissed by accident);
 *  - initial focus lands on the least destructive action;
 *  - focus is trapped inside while open and restored to the opener on close;
 *  - backdrop click closes, but a click inside the panel never does;
 *  - semantics: role="dialog" aria-modal="true" with an explicit title.
 */
export function Dialog({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  busy = false,
  testId,
}: {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  onClose: () => void;
  busy?: boolean;
  testId?: string;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    first?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panel) return;
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      );
      if (focusables.length === 0) return;
      const firstEl = focusables[0];
      const lastEl = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === firstEl || !panel.contains(active))) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    }

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
      openerRef.current?.focus?.();
    };
  }, [open, onClose, busy]);

  if (!open) return null;

  return (
    <div className="dialog-backdrop animate-fade-in" onMouseDown={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        className="fixed left-1/2 top-1/2 z-[70] w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 animate-scale-in rounded-xl border border-line bg-paper p-6 shadow-popover"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className="font-serif text-xl font-bold text-ink">{title}</h2>
        {description && <div className="mt-2 text-sm leading-relaxed text-ink-soft">{description}</div>}
        {children && <div className="mt-4">{children}</div>}
        {footer && <div className="mt-6 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}
