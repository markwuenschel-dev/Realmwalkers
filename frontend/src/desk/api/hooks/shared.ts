import type { DraftQueueBlockerOut } from "../types";

/** Surface API failures as user-visible error strings. */
export type DeskFail = (e: unknown) => void;

export function toDeskError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The two actionable 409 bodies this API raises, as they arrive on the wire. FastAPI wraps every
 *  `HTTPException` payload in `detail` and this app registers no custom exception handler, so the
 *  payload is always one level down — the backend asserts exactly that shape (e.g.
 *  `tests/test_adoption_start.py:167`, `resp.json()["detail"]["reason"]`). */
export interface ConflictDetail {
  /** Draft/redraft queue refusals — `chapters.py` `schedule`/`draft_chapter`. */
  blockers?: DraftQueueBlockerOut[];
  /** Chapter-workflow refusals — `chapter_has_contracted_scenes`, `chapter_contract_already_approved`,
   *  `chapter_workflow_busy`. Carry prose written for the author; show it verbatim. */
  reason?: string;
  message?: string;
  /** Amendment drift only (`amendment_source_drifted`, `api/routers/packets.py:383-395`): the prose
   *  fingerprint the amendment was authored against vs the chapter's fingerprint now. Extra keys that
   *  ride ALONGSIDE `message` — never in place of it. */
  expected?: string | null;
  actual?: string | null;
}

/** The parsed 409 body, for callers that must branch on the machine `reason` token rather than only
 *  show the sentence — e.g. amendment recovery, where `amendment_source_drifted` (nothing was written,
 *  re-run against current prose) and `amendment_already_open` (go review the existing branch) need
 *  different instructions. Null when `e` is not a 409 carrying an object detail. */
export function conflictDetail(e: unknown): ConflictDetail | null {
  const err = e as { status?: number; data?: { detail?: unknown } } | null;
  if (!err || err.status !== 409) return null;
  const detail = err.data?.detail;
  return detail && typeof detail === "object" ? (detail as ConflictDetail) : null;
}

/** If `e` is a 409 conflict, return a concise, human summary of the actionable reason — else null, so
 *  the caller falls back to the raw error message. Duck-typed on the ApiError shape (status + parsed
 *  data) so it also works where the client is mocked. */
export function conflictMessage(e: unknown): string | null {
  const detail = conflictDetail(e);
  if (!detail) return null;
  const blockers = detail.blockers;
  if (blockers?.length) {
    return blockers
      .map((b) =>
        b.scene_no != null ? `Scene ${b.scene_no}: ${b.required_action}` : b.required_action,
      )
      .join(" · ");
  }
  return detail.message ?? null;
}

export interface DeskErrorState {
  error: string | null;
  setError: (msg: string | null) => void;
  clearError: () => void;
  fail: DeskFail;
}
