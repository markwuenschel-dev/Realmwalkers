"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { Spinner } from "../components/DraftActivity";
import { TotalsStrip, TotalsTable, fmtTokens } from "../components/Telemetry";
import type {
  BookTelemetryOut,
  ChapterRollupOut,
  RunRollupOut,
  TelemetryGroupOut,
} from "../api/types";

// "2026-06-28 14:07" in local time — compact enough for a table cell; falls back to the run id.
function fmtRun(r: RunRollupOut): string {
  const label =
    r.chapter_no != null ? `Ch ${r.chapter_no}` : (r.title ?? r.run_id?.slice(0, 8) ?? "—");
  if (!r.started_at) return `${label} · (legacy)`;
  const d = new Date(r.started_at);
  const stamp = `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  return `${stamp} · ${label}`;
}

// The global Telemetry tab: persisted LLM-call cost/cache/health for the active book, rolled up so a
// human can compare across chapters, models, and stages (the scene-packet Author/QA, and any later
// instrumented call site). This is the "own tab" cross-chapter view — the per-chapter slice lives
// under the scene packets. Read-only: telemetry is produced by the workers, never edited here.

// Runs come back newest-first one page at a time; "Load older runs" appends the next page.
const RUN_PAGE = 5;

export default function TelemetryScreen() {
  const { bookId } = useDeskData();
  const [data, setData] = useState<BookTelemetryOut | null>(null);
  // Accumulated run rows across paged fetches (data.by_run is only ever the latest page).
  const [runs, setRuns] = useState<RunRollupOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    try {
      const d = await api.bookTelemetry(bookId, { limit: RUN_PAGE, offset: 0 });
      setData(d);
      setRuns(d.by_run);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  // Fetch the next page of runs (offset = rows already shown) and append them. Totals and the other
  // rollups are full-book already, so we only consume by_run / run_total from the follow-up fetch.
  const loadMore = useCallback(async () => {
    if (!bookId) return;
    setLoadingMore(true);
    try {
      const d = await api.bookTelemetry(bookId, { limit: RUN_PAGE, offset: runs.length });
      setRuns((prev) => [...prev, ...d.by_run]);
      setData((cur) => (cur ? { ...cur, run_total: d.run_total } : d));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [bookId, runs.length]);

  useEffect(() => {
    void load();
  }, [load]);

  const empty = data && data.totals.calls === 0;

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:18px",
        )}
      >
        <div>
          <h1
            style={css(
              "margin:0 0 4px;font-family:var(--display);font-weight:600;font-size:26px;color:var(--ink)",
            )}
          >
            Telemetry
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:13.5px;max-width:640px")}>
            Persisted per-call LLM cost and cache efficiency for this book — compare across
            chapters, models, and pipeline stages. Truncations and errors flag where a derive failed
            and why.
          </p>
        </div>
        <button
          disabled={loading}
          onClick={() => void load()}
          style={css(
            `height:30px;padding:0 14px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12.5px;cursor:${loading ? "default" : "pointer"}`,
          )}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div
          style={css(
            "margin-bottom:14px;border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg2));border-radius:9px;padding:10px 12px;color:var(--bad);font-size:12.5px",
          )}
        >
          {error}
        </div>
      )}

      {!data && loading ? (
        <div
          style={css(
            "display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:12px;color:var(--dim)",
          )}
        >
          <Spinner /> loading telemetry…
        </div>
      ) : empty ? (
        <div
          style={css(
            "border:1px dashed var(--line);border-radius:11px;padding:24px;text-align:center;color:var(--dim);font-size:13px",
          )}
        >
          No model calls recorded yet. Derive scene packets for a chapter and they'll show up here.
        </div>
      ) : data ? (
        <div style={css("display:flex;flex-direction:column;gap:8px")}>
          <TotalsStrip t={data.totals} />

          <Section label="By chapter">
            <TotalsTable<ChapterRollupOut>
              label="Chapter"
              rows={data.by_chapter}
              nameOf={(r) =>
                r.chapter_no != null
                  ? `Ch ${r.chapter_no}${r.title ? ` · ${r.title}` : ""}`
                  : (r.title ?? r.chapter_id.slice(0, 8))
              }
            />
          </Section>

          <Section label="By run (newest first)">
            <TotalsTable<RunRollupOut>
              label="Run"
              rows={runs}
              nameOf={fmtRun}
              emptyText="No derive runs recorded yet."
            />
            {runs.length < data.run_total && (
              <div style={css("display:flex;justify-content:center;margin-top:8px")}>
                <button
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                  style={css(
                    `height:30px;padding:0 14px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12.5px;cursor:${loadingMore ? "default" : "pointer"}`,
                  )}
                >
                  {loadingMore
                    ? "Loading…"
                    : `Load older runs (${data.run_total - runs.length} more)`}
                </button>
              </div>
            )}
          </Section>

          <div
            style={css(
              "display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px",
            )}
          >
            <Section label="By model">
              <TotalsTable<TelemetryGroupOut>
                label="Model"
                rows={data.by_model}
                nameOf={(r) => r.key}
              />
            </Section>
            <Section label="By stage">
              <TotalsTable<TelemetryGroupOut>
                label="Stage"
                rows={data.by_stage}
                nameOf={(r) => r.key.replace(/_/g, " ")}
              />
            </Section>
          </div>

          <p
            style={css(
              "margin:6px 2px 0;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
            )}
          >
            {fmtTokens(data.totals.cache_tokens_saved)} tokens recovered from cache across{" "}
            {data.totals.calls} calls.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div
        style={css(
          "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin:16px 0 7px",
        )}
      >
        {label}
      </div>
      {children}
    </div>
  );
}
