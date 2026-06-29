/** Surface API failures as user-visible error strings. */
export type DeskFail = (e: unknown) => void;

export function toDeskError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export interface DeskErrorState {
  error: string | null;
  setError: (msg: string | null) => void;
  clearError: () => void;
  fail: DeskFail;
}
