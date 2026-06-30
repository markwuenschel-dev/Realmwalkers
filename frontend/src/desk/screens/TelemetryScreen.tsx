"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { Spinner } from "../components/DraftActivity";
import { TotalsStrip, TotalsTable, fmtTokens } from "../components/Telemetry";
import { ProblemsPanel } from "../components/telemetry/ProblemsPanel";
import {
  TelemetryFiltersBar,
  stageOptionsFromBook,
} from "../components/telemetry/TelemetryFiltersBar";
import { TelemetryDrawer, useTelemetryDrawer } from "../components/telemetry/TelemetryDrawer";
import type {
  BookTelemetryOut,
  ChapterRollupOut,
  RunRollupOut,
  RunTelemetryOut,
  SceneTelemetryOut,
  TelemetryGroupOut,
} from "../api/types";
import type { TelemetryDrawerView } from "../components/telemetry/types";
import type { LlmCallFilters } from "../components/telemetry/telemetryFilters";
import {
  filtersFromSearchParams,
  filtersLabel,
  filtersToSearchParams,
  hasActiveFilters,
} from "../components/telemetry/telemetryFilters";

function fmtRun(r: RunRollupOut): string {
  const label =
    r.chapter_no != null ? `Ch ${r.chapter_no}` : (r.title ?? r.run_id?.slice(0, 8) ?? "—");
  if (!r.started_at) return `${label} · (legacy)`;
  const d = new Date(r.started_at);
  const stamp = `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  return `${stamp} · ${label}`;
}

const RUN_PAGE = 5;

export default function TelemetryScreen() {
  const { bookId, chapters } = useDeskData();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const drawer = useTelemetryDrawer();
  const [data, setData] = useState<BookTelemetryOut | null>(null);
  const [runs, setRuns] = useState<RunRollupOut[]>([]);
  const [latestRun, setLatestRun] = useState<RunTelemetryOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareA, setCompareA] = useState<string | null>(null);
  const [filters, setFilters] = useState<LlmCallFilters>({});
  const [problemsReloadKey, setProblemsReloadKey] = useState(0);

  const stageHints = useMemo(() => stageOptionsFromBook(data), [data]);
  const latestRunId = runs[0]?.run_id ?? latestRun?.run_id ?? null;

  // Restore filters from URL (?book=…&truncated=1&…).
  useEffect(() => {
    if (!bookId) return;
    const urlBook = searchParams.get("book");
    if (urlBook && urlBook !== bookId) return;
    const fromUrl = filtersFromSearchParams(searchParams);
    if (hasActiveFilters(fromUrl)) {
      setFilters(fromUrl);
    }
  }, [bookId, searchParams]);

  const syncUrl = useCallback(
    (f: LlmCallFilters) => {
      if (!bookId) return;
      const p = hasActiveFilters(f) ? filtersToSearchParams(bookId, f) : new URLSearchParams();
      if (!hasActiveFilters(f)) p.delete("book");
      const q = p.toString();
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [bookId, pathname, router],
  );

  const openView = useCallback(
    (view: TelemetryDrawerView) => {
      drawer.open(view);
    },
    [drawer],
  );

  const applyFilters = useCallback(
    (f: LlmCallFilters) => {
      if (!bookId) return;
      syncUrl(f);
      openView({ kind: "filtered", label: filtersLabel(f), bookId, filters: f });
    },
    [bookId, openView, syncUrl],
  );

  const clearFilters = useCallback(() => {
    setFilters({});
    syncUrl({});
  }, [syncUrl]);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    try {
      const d = await api.bookTelemetry(bookId, { limit: RUN_PAGE, offset: 0 });
      setData(d);
      setRuns(d.by_run);
      setError(null);
      const firstRun = d.by_run[0]?.run_id;
      if (firstRun) {
        api
          .runTelemetry(firstRun)
          .then(setLatestRun)
          .catch(() => setLatestRun(null));
      } else {
        setLatestRun(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [bookId]);

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

  const onDataChanged = useCallback(async () => {
    await load();
    setProblemsReloadKey((k) => k + 1);
  }, [load]);

  const onRunClick = useCallback(
    (r: RunRollupOut) => {
      if (!r.run_id || !bookId) return;
      if (compareMode) {
        if (compareA === null) {
          setCompareA(r.run_id);
          return;
        }
        if (compareA === r.run_id) {
          setCompareA(null);
          return;
        }
        openView({ kind: "compare", runA: compareA, runB: r.run_id, bookId });
        setCompareA(null);
        setCompareMode(false);
        return;
      }
      openView({ kind: "run", runId: r.run_id });
    },
    [bookId, compareA, compareMode, openView],
  );

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
            Operations console for LLM calls — drill into runs, scenes, and individual calls. Click
            any row to inspect; click two runs to compare.
            {compareMode && (
              <span style={css("color:var(--warn, #e8a020)")}> Select two runs to compare…</span>
            )}
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
      ) : data && bookId ? (
        <div style={css("display:flex;flex-direction:column;gap:8px")}>
          <ProblemsPanel bookId={bookId} onOpen={openView} reloadKey={problemsReloadKey} />
          <TelemetryFiltersBar
            bookId={bookId}
            chapters={chapters}
            runs={runs}
            latestRunId={latestRunId}
            stageHints={stageHints}
            value={filters}
            selectedRunId={filters.run_id}
            onChange={setFilters}
            onApply={applyFilters}
            onClear={clearFilters}
            onDataChanged={onDataChanged}
          />
          <TotalsStrip t={data.totals} />

          {latestRun && latestRun.scenes.length > 0 && (
            <Section label={`By scene · latest run`}>
              <TotalsTable<SceneTelemetryOut>
                label="Scene"
                rows={latestRun.scenes}
                rowKey={(r) => String(r.scene_no)}
                nameOf={(r) => (
                  <span>
                    Sc{r.scene_no ?? "—"}{" "}
                    <span
                      style={css(
                        `color:${r.status === "error" ? "var(--bad)" : r.status === "warn" ? "var(--warn, #e8a020)" : "var(--ok)"}`,
                      )}
                    >
                      ({r.status})
                    </span>
                  </span>
                )}
                onRowClick={(r) => {
                  if (r.scene_no != null && latestRun.run_id)
                    openView({ kind: "scene", runId: latestRun.run_id, sceneNo: r.scene_no });
                }}
              />
            </Section>
          )}

          <Section label="By chapter">
            <TotalsTable<ChapterRollupOut>
              label="Chapter"
              rows={data.by_chapter}
              nameOf={(r) =>
                r.chapter_no != null
                  ? `Ch ${r.chapter_no}${r.title ? ` · ${r.title}` : ""}`
                  : (r.title ?? r.chapter_id.slice(0, 8))
              }
              onRowClick={(r) =>
                openView({
                  kind: "chapter",
                  chapterId: r.chapter_id,
                  bookId,
                  chapterNo: r.chapter_no,
                  title: r.title,
                })
              }
            />
          </Section>

          <Section label="By run (newest first)">
            <TotalsTable<RunRollupOut>
              label="Run"
              rows={runs}
              rowKey={(r) => r.run_id ?? "legacy"}
              nameOf={fmtRun}
              emptyText="No derive runs recorded yet."
              onRowClick={(r) => r.run_id && onRunClick(r)}
            />
            <div style={css("display:flex;gap:8px;margin-top:8px;flex-wrap:wrap")}>
              <button
                type="button"
                onClick={() => {
                  setCompareMode((m) => !m);
                  setCompareA(null);
                }}
                style={css(
                  "height:28px;padding:0 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:12px;cursor:pointer",
                )}
              >
                {compareMode ? "Cancel compare" : "Compare runs"}
              </button>
              {runs.length < data.run_total && (
                <button
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                  style={css(
                    `height:28px;padding:0 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:12px;cursor:${loadingMore ? "default" : "pointer"}`,
                  )}
                >
                  {loadingMore
                    ? "Loading…"
                    : `Load older runs (${data.run_total - runs.length} more)`}
                </button>
              )}
            </div>
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
                onRowClick={(r) => openView({ kind: "model", model: r.key, bookId })}
              />
            </Section>
            <Section label="By stage">
              <TotalsTable<TelemetryGroupOut>
                label="Stage"
                rows={data.by_stage}
                nameOf={(r) => r.key.replace(/_/g, " ")}
                onRowClick={(r) => openView({ kind: "stage", stage: r.key, bookId })}
              />
            </Section>
          </div>

          <p
            style={css(
              "margin:6px 2px 0;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
            )}
          >
            {fmtTokens(data.totals.cache_tokens_saved)} tokens recovered from cache · est. $
            {data.totals.estimated_cost_usd?.toFixed(2) ?? "0.00"} across {data.totals.calls} calls.
          </p>
        </div>
      ) : null}

      {drawer.isOpen && bookId && drawer.view && (
        <TelemetryDrawer nav={drawer.nav} bookId={bookId} />
      )}
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
