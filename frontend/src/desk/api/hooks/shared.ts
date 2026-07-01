import type { DraftQueueBlockerOut } from "../types";

/** Surface API failures as user-visible error strings. */
export type DeskFail = (e: unknown) => void;

export function toDeskError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** If `e` is a 409 draft/redraft conflict (an ApiError carrying blockers), return a concise, human
 *  summary of the actionable reasons — else null, so the caller falls back to the raw error message.
 *  Duck-typed on the ApiError shape (status + parsed data) so it also works where the client is mocked. */
export function draftBlockerMessage(e: unknown): string | null {
  const err = e as { status?: number; data?: { blockers?: DraftQueueBlockerOut[] } } | null;
  if (!err || err.status !== 409) return null;
  const blockers = err.data?.blockers;
  if (!blockers?.length) return null;
  return blockers
    .map((b) =>
      b.scene_no != null ? `Scene ${b.scene_no}: ${b.required_action}` : b.required_action,
    )
    .join(" · ");
}

export interface DeskErrorState {
  error: string | null;
  setError: (msg: string | null) => void;
  clearError: () => void;
  fail: DeskFail;
}
