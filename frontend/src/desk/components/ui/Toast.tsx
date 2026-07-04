import type { ReactNode } from "react";
import { css } from "../../css";

export type ToastTone = "success" | "error" | "warn" | "info";

export interface ToastItem {
  id: string;
  tone: ToastTone;
  message: string;
  /** Optional action ("View" → open the Activity drawer, "Back to inbox", …). */
  action?: { label: string; onClick: () => void };
}

const TONE_VAR: Record<ToastTone, string> = {
  success: "--good",
  error: "--bad",
  warn: "--warn",
  info: "--info",
};

/** One toast card — presentational only; lifetimes live in the toast hook. */
export function Toast({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: string) => void }) {
  const v = TONE_VAR[toast.tone];
  return (
    <div
      role="status"
      style={css(
        `display:flex;align-items:center;gap:12px;max-width:420px;padding:12px 15px;border-radius:var(--r);background:var(--bg2);border:1px solid color-mix(in srgb,var(${v}) 40%,var(--line));border-left:3px solid var(${v});box-shadow:var(--shadow2);animation:fadeUp var(--dur) var(--ease-out)`,
      )}
    >
      {toast.tone === "success" && (
        <span
          style={css(
            "display:flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:var(--good);flex:none;animation:checkPop 200ms var(--ease-out)",
          )}
        >
          <svg width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden>
            <path
              d="M1.5 5.5l2.3 2.3L8.5 2.5"
              stroke="var(--bg2)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      )}
      <span
        style={css(
          "font-family:var(--ui);font-size:13px;line-height:1.45;color:var(--ink);min-width:0",
        )}
      >
        {toast.message}
      </span>
      {toast.action && (
        <button
          className="dk-btn"
          onClick={toast.action.onClick}
          style={css(
            `flex:none;border:1px solid var(--line);background:var(--bg3);color:var(${v});border-radius:7px;padding:4px 10px;font-family:var(--ui);font-size:12px;cursor:pointer`,
          )}
        >
          {toast.action.label}
        </button>
      )}
      <button
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss"
        style={css(
          "flex:none;border:none;background:none;color:var(--dim);cursor:pointer;font-size:14px;line-height:1;padding:2px",
        )}
      >
        ×
      </button>
    </div>
  );
}

/** Bottom-right stack, newest last, capped by the hook (max 3). */
export function ToastHost({
  toasts,
  onDismiss,
  children,
}: {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
  children?: ReactNode;
}) {
  if (toasts.length === 0 && !children) return null;
  return (
    <div
      className="no-print"
      style={css(
        "position:fixed;right:20px;bottom:20px;z-index:60;display:flex;flex-direction:column;gap:10px;align-items:flex-end",
      )}
    >
      {toasts.map((t) => (
        <Toast key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
      {children}
    </div>
  );
}
