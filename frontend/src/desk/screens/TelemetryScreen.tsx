"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { Button, Eyebrow, Panel, Skeleton } from "../components/ui";
import { useTabLoadTiming } from "../lib/useTabLoadTiming";
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
  EditorialAgentRunOut,
  ProductionRunRollupOut,
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

function fmtProductionRun(r: ProductionRunRollupOut): string {
  const label =
    r.chapter_no != null ? `Ch ${r.chapter_no}` : (r.production_run_id?.slice(0, 8) ?? "—");
  return r.status ? `${label} · ${r.status}` : label;
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

// AgentRun status tone (queued/running/succeeded/failed…), distinct from the ok/warn/error scene tone.
function agentStatusColor(status: string): string {
  if (status === "failed" || status === "error") return "var(--bad)";
  if (status === "running" || status === "queued") return "var(--warn)";
  return "var(--ok)";
}

// The editorial pipeline's deterministic orchestration agents (contract_classifier, repair_scheduler,
// …). They make no model call — every row is $0 / no tokens — so this is an activity list, not a cost
// table: it shows the pipeline ran, with per-step duration and an explicit deterministic label.
function EditorialPipelinePanel({ rows }: { rows: EditorialAgentRunOut[] }) {
  if (rows.length === 0) return null;
  const cell = "padding:6px 10px;font-family:var(--mono);font-size:11.5px";
  const head =
    "padding:6px 10px;font-family:var(--mono);font-size:9.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim)";
  return (
    <Panel inset pad="14px 16px" eyebrow="Editorial pipeline · deterministic agents">
      <p style={css("margin:0 0 8px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
        Orchestration steps for this book's production runs — deterministic, so $0 · no tokens.
      </p>
      <div style={css("overflow:auto;border:1px solid var(--line);border-radius:10px")}>
        <table style={css("width:100%;border-collapse:collapse")}>
          <thead>
            <tr style={css("background:var(--bg2)")}>
              <th style={css(`${head};text-align:left`)}>Agent</th>
              <th style={css(`${head};text-align:left`)}>Stage</th>
              <th style={css(`${head};text-align:left`)}>Status</th>
              <th style={css(`${head};text-align:right`)}>Duration</th>
              <th style={css(`${head};text-align:right`)}>Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.production_run_id ?? "—"}-${i}`}
                style={css("border-top:1px solid var(--line)")}
              >
                <td style={css(`${cell};color:var(--ink)`)}>{r.agent_name}</td>
                <td style={css(`${cell};color:var(--dim)`)}>{r.stage.replace(/_/g, " ")}</td>
                <td style={css(`${cell};color:${agentStatusColor(r.status)}`)}>{r.status}</td>
                <td style={css(`${cell};text-align:right;color:var(--dim)`)}>
                  {fmtDuration(r.duration_ms)}
                </td>
                <td style={css(`${cell};text-align:right;color:var(--dim)`)}>$0 · deterministic</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const RUN_PAGE = 5;

// Session cache keyed by book: the last-loaded aggregates. Route pages unmount per tab switch, so
// without this every Telemetry revisit started from a blank spinner and refetched everything. Now a
// revisit paints the cached aggregates instantly and load() revalidates in the background. Staleness:
// telemetry only ever grows (LLM calls append) and every in-app mutation (run/telemetry deletes) goes
// through onDataChanged → load(), which rewrites this cache — so briefly-stale totals during the
// background refetch are the worst case.
interface TelemetryCacheEntry {
  data: BookTelemetryOut;
  runs: RunRollupOut[];
  latestRun: RunTelemetryOut | null;
}
const telemetryCache = new Map<string, TelemetryCacheEntry>();

/** Test-only: module-level session state would otherwise leak between vitest cases. */
export function resetTelemetryCacheForTests(): void {
  telemetryCache.clear();
}

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
      telemetryCache.set(bookId, { data: d, runs: d.by_run, latestRun: null });
      const firstRun = d.by_run[0]?.run_id;
      if (firstRun) {
        api
          .runTelemetry(firstRun)
          .then((lr) => {
            setLatestRun(lr);
            const c = telemetryCache.get(bookId);
            // Only attach to the entry this load wrote — a newer load's entry keeps its own fetch.
            if (c?.data === d) telemetryCache.set(bookId, { ...c, latestRun: lr });
          })
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
      const nextRuns = [...runs, ...d.by_run];
      setRuns(nextRuns);
      setData((cur) => (cur ? { ...cur, run_total: d.run_total } : d));
      const c = telemetryCache.get(bookId);
      if (c) {
        telemetryCache.set(bookId, {
          ...c,
          runs: nextRuns,
          data: { ...c.data, run_total: d.run_total },
        });
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [bookId, runs]);

  // Instant paint on tab revisit: hydrate from the session cache (if any), then revalidate in the
  // background. With no cache this is just the normal first load — the full-screen spinner only
  // shows while `data` is null, so a cached revisit never blanks.
  useEffect(() => {
    if (!bookId) return;
    const cached = telemetryCache.get(bookId);
    if (cached) {
      setData(cached.data);
      setRuns(cached.runs);
      setLatestRun(cached.latestRun);
    }
    void load();
  }, [bookId, load]);

  // Tab-switch cost, visible in the console: ~0ms when the cache hydrates, network time when cold.
  useTabLoadTiming("telemetry", data != null);

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
          <Eyebrow style="margin-bottom:6px">Operations · LLM calls</Eyebrow>
          <h1
            style={css(
              "margin:0 0 6px;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)",
            )}
          >
            Telemetry
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:13.5px;max-width:640px")}>
            Operations console for LLM calls — drill into runs, scenes, and individual calls. Click
            any row to inspect; click two runs to compare.
            {compareMode && (
              <span style={css("color:var(--warn)")}> Select two runs to compare…</span>
            )}
          </p>
        </div>
        <Button size="sm" disabled={loading} onClick={() => void load()}>
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
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
        <div style={css("display:flex;flex-direction:column;gap:14px")}>
          <Skeleton height="72px" />
          <Skeleton lines={8} />
        </div>
      ) : empty ? (
        <div style={css("text-align:center;padding:90px 24px")}>
          <div aria-hidden style={css("font-size:20px;color:var(--accent);margin-bottom:16px")}>
            ✦
          </div>
          <p
            style={css(
              "margin:0;font-family:var(--display);font-style:italic;font-size:18px;line-height:1.6;color:var(--dim)",
            )}
          >
            No model calls recorded yet. Derive scene packets for a chapter and they'll show up
            here.
          </p>
        </div>
      ) : data && bookId ? (
        <div style={css("display:flex;flex-direction:column;gap:14px")}>
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
            <Panel inset pad="14px 16px" eyebrow="By scene · latest run">
              <TotalsTable<SceneTelemetryOut>
                label="Scene"
                rows={latestRun.scenes}
                rowKey={(r) => String(r.scene_no)}
                nameOf={(r) => (
                  <span>
                    Sc{r.scene_no ?? "—"}{" "}
                    <span
                      style={css(
                        `color:${r.status === "error" ? "var(--bad)" : r.status === "warn" ? "var(--warn)" : "var(--ok)"}`,
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
            </Panel>
          )}

          <Panel inset pad="14px 16px" eyebrow="By chapter">
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
          </Panel>

          {data.by_production_run.length > 0 && (
            <Panel inset pad="14px 16px" eyebrow="By production run · cost per run">
              <TotalsTable<ProductionRunRollupOut>
                label="Production run"
                rows={data.by_production_run}
                rowKey={(r) => r.production_run_id ?? "—"}
                nameOf={fmtProductionRun}
                emptyText="No production-run spend recorded yet."
              />
            </Panel>
          )}

          <Panel inset pad="14px 16px" eyebrow="By run (newest first)">
            <TotalsTable<RunRollupOut>
              label="Run"
              rows={runs}
              rowKey={(r) => r.run_id ?? "legacy"}
              nameOf={fmtRun}
              emptyText="No derive runs recorded yet."
              onRowClick={(r) => r.run_id && onRunClick(r)}
            />
            <div style={css("display:flex;gap:8px;margin-top:10px;flex-wrap:wrap")}>
              <Button
                size="sm"
                onClick={() => {
                  setCompareMode((m) => !m);
                  setCompareA(null);
                }}
              >
                {compareMode ? "Cancel compare" : "Compare runs"}
              </Button>
              {runs.length < data.run_total && (
                <Button size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
                  {loadingMore
                    ? "Loading…"
                    : `Load older runs (${data.run_total - runs.length} more)`}
                </Button>
              )}
            </div>
          </Panel>

          <div
            style={css(
              "display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px",
            )}
          >
            <Panel inset pad="14px 16px" eyebrow="By model">
              <TotalsTable<TelemetryGroupOut>
                label="Model"
                rows={data.by_model}
                nameOf={(r) => r.key}
                onRowClick={(r) => openView({ kind: "model", model: r.key, bookId })}
              />
            </Panel>
            <Panel inset pad="14px 16px" eyebrow="By stage">
              <TotalsTable<TelemetryGroupOut>
                label="Stage"
                rows={data.by_stage}
                nameOf={(r) => r.key.replace(/_/g, " ")}
                onRowClick={(r) => openView({ kind: "stage", stage: r.key, bookId })}
              />
            </Panel>
            <Panel inset pad="14px 16px" eyebrow="Draft vs revision">
              <TotalsTable<TelemetryGroupOut>
                label="Kind"
                rows={data.by_kind}
                nameOf={(r) => (r.key === "revision" ? "Revision (repair)" : "Draft (original)")}
                emptyText="No drafting or revision calls recorded yet."
              />
            </Panel>
          </div>

          <EditorialPipelinePanel rows={data.editorial_runs} />

          <ProblemsPanel bookId={bookId} onOpen={openView} reloadKey={problemsReloadKey} />

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
