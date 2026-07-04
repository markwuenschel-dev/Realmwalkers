import { useCallback, useMemo, useRef, useState } from "react";
import type { ToastItem } from "../../components/ui/Toast";

export interface DeskToastsState {
  toasts: ToastItem[];
  pushToast: (toast: Omit<ToastItem, "id">) => void;
  dismissToast: (id: string) => void;
}

// Completion/status toasts (bottom-right). Success/info auto-dismiss after 5s, error/warn after
// 8s; at most 3 on screen (oldest dropped) so a busy queue can't wallpaper the corner.
const AUTO_DISMISS_MS: Record<ToastItem["tone"], number> = {
  success: 5000,
  info: 5000,
  warn: 8000,
  error: 8000,
};
const MAX_TOASTS = 3;

export function useDeskToasts(): DeskToastsState {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const timers = useRef(new Map<string, number>());

  const dismissToast = useCallback((id: string) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const h = timers.current.get(id);
    if (h) {
      window.clearTimeout(h);
      timers.current.delete(id);
    }
  }, []);

  const pushToast = useCallback(
    (toast: Omit<ToastItem, "id">) => {
      const id = `toast-${++seq.current}`;
      setToasts((list) => [...list.slice(-(MAX_TOASTS - 1)), { ...toast, id }]);
      timers.current.set(
        id,
        window.setTimeout(() => dismissToast(id), AUTO_DISMISS_MS[toast.tone]),
      );
    },
    [dismissToast],
  );

  return useMemo(() => ({ toasts, pushToast, dismissToast }), [toasts, pushToast, dismissToast]);
}
