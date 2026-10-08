"use client";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * Toast notifications (Phase 9) — one shared way to report the outcome of an
 * action anywhere in the app.
 *
 *  - success/info use role="status" (polite), errors use role="alert";
 *  - each toast auto-dismisses, but the timer pauses while it is hovered or
 *    focused so a message can always be read and copied;
 *  - rendered in a fixed region that never covers the top navigation.
 */
export type ToastKind = "success" | "error" | "info";

type Toast = { id: number; kind: ToastKind; message: string };

type ToastApi = {
  push: (kind: ToastKind, message: string) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

const AUTODISMISS_MS: Record<ToastKind, number> = {
  success: 4000,
  info: 5000,
  error: 9000,
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const push = useCallback((kind: ToastKind, message: string) => {
    seq.current += 1;
    const id = seq.current;
    setToasts((prev) => [...prev, { id, kind, message }].slice(-4));
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (m) => push("success", m),
      error: (m) => push("error", m),
      info: (m) => push("info", m),
    }),
    [push]
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <ToastItem
            key={t.id}
            toast={t}
            onDone={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  const [paused, setPaused] = useState(false);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (paused) return;
    const t = setTimeout(() => doneRef.current(), AUTODISMISS_MS[toast.kind]);
    return () => clearTimeout(t);
  }, [paused, toast.kind]);

  const icon = toast.kind === "success" ? "✓" : toast.kind === "error" ? "!" : "i";
  const accent =
    toast.kind === "success"
      ? "bg-emerald-500"
      : toast.kind === "error"
      ? "bg-brand-600"
      : "bg-gold-500";

  return (
    <div
      className="toast pointer-events-auto flex items-start gap-3 animate-scale-in"
      role={toast.kind === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span
        className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white ${accent}`}
        aria-hidden="true"
      >
        {icon}
      </span>
      <p className="flex-1 leading-snug">{toast.message}</p>
      <button
        type="button"
        onClick={onDone}
        aria-label="Dismiss notification"
        className="rounded px-1 text-cream-300 transition hover:text-white"
      >
        ✕
      </button>
    </div>
  );
}
