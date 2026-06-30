"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../../css";
import { api } from "../../api/client";
import type { BookTelemetryOut, ChapterOut, RunRollupOut } from "../../api/types";
import {
  buildFiltersFromForm,
  filtersLabel,
  formFromFilters,
  hasActiveFilters,
  presetFilters,
  type FilterPresetId,
  type LlmCallFilters,
} from "./telemetryFilters";

const inputStyle = css(
  "height:28px;padding:0 8px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--mono);font-size:11px;min-width:0",
);

const dangerBtn = css(
  "height:28px;padding:0 12px;border-radius:8px;border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg3));color:var(--bad);font-size:12px;cursor:pointer",
);

const PRESETS: { id: FilterPresetId; label: string }[] = [
  { id: "problems", label: "Show problems" },
  { id: "scene_packet", label: "ScenePacket" },
  { id: "draft", label: "Draft calls" },
  { id: "cache_primes", label: "Cache primes" },
  { id: "latest_run", label: "Latest run" },
  { id: "this_chapter", label: "This chapter" },
];

const GLOBAL_CONFIRM_PHRASE = "DELETE ALL TELEMETRY";

export function TelemetryFiltersBar({
  bookId,
  chapters,
  runs,
  latestRunId,
  stageHints,
  value,
  selectedRunId,
  onChange,
  onApply,
  onClear,
  onDataChanged,
}: {
  bookId: string;
  chapters: ChapterOut[];
  runs: RunRollupOut[];
  latestRunId?: string | null;
  stageHints?: string[];
  value: LlmCallFilters;
  selectedRunId?: string | null;
  onChange: (f: LlmCallFilters) => void;
  onApply: (f: LlmCallFilters) => void;
  onClear: () => void;
  onDataChanged: () => void | Promise<void>;
}) {
  const [chapterId, setChapterId] = useState("");
  const [runId, setRunId] = useState("");
  const [sceneNo, setSceneNo] = useState("");
  const [stage, setStage] = useState("");
  const [model, setModel] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [fallbacksOnly, setFallbacksOnly] = useState(false);
  const [expensive, setExpensive] = useState(false);
  const [cacheMiss, setCacheMiss] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [showGlobalConfirm, setShowGlobalConfirm] = useState(false);
  const [globalConfirmText, setGlobalConfirmText] = useState("");

  const effectiveRunId = selectedRunId ?? (runId || null);

  useEffect(() => {
    const f = formFromFilters(value);
    setChapterId(f.chapter_id);
    setRunId(f.run_id);
    setSceneNo(f.scene_no);
    setStage(f.stage);
    setModel(f.model);
    setTruncated(f.truncated);
    setErrorsOnly(f.errors_only);
    setFallbacksOnly(f.fallbacks_only);
    setExpensive(f.expensive);
    setCacheMiss(f.cache_miss_only);
  }, [value]);

  const applyForm = useCallback(() => {
    const f = buildFiltersFromForm({
      chapter_id: chapterId,
      run_id: runId,
      scene_no: sceneNo,
      stage,
      model,
      truncated,
      errors_only: errorsOnly,
      fallbacks_only: fallbacksOnly,
      expensive,
      cache_miss_only: cacheMiss,
    });
    onChange(f);
    onApply(f);
  }, [
    chapterId,
    runId,
    sceneNo,
    stage,
    model,
    truncated,
    errorsOnly,
    fallbacksOnly,
    expensive,
    cacheMiss,
    onChange,
    onApply,
  ]);

  const applyPreset = useCallback(
    (id: FilterPresetId) => {
      const base = presetFilters(id, {
        latestRunId,
        chapterId: chapterId || chapters[0]?.id,
      });
      onChange(base);
      onApply(base);
    },
    [chapterId, chapters, latestRunId, onApply, onChange],
  );

  const runDelete = useCallback(
    async (label: string, action: () => Promise<unknown>) => {
      setDeleteBusy(label);
      setDeleteError(null);
      try {
        await action();
        await onDataChanged();
        if (label === "run" && runId) {
          setRunId("");
          onChange(buildFiltersFromForm({ ...formFromFilters(value), run_id: "" }));
        }
      } catch (e) {
        setDeleteError(e instanceof Error ? e.message : String(e));
      } finally {
        setDeleteBusy(null);
      }
    },
    [onChange, onDataChanged, runId, value],
  );

  const deleteThisRun = () => {
    if (!effectiveRunId) return;
    if (!confirm(`Delete all telemetry for run ${effectiveRunId.slice(0, 8)}…? This cannot be undone.`))
      return;
    void runDelete("run", () => api.deleteRunTelemetry(bookId, effectiveRunId));
  };

  const clearBook = () => {
    if (!confirm("Delete all telemetry for this book? Agent health stats will reset. This cannot be undone."))
      return;
    void runDelete("book", () => api.deleteBookTelemetry(bookId));
  };

  const clearAll = () => {
    if (globalConfirmText.trim() !== GLOBAL_CONFIRM_PHRASE) return;
    void runDelete("global", () => api.deleteAllTelemetry("DELETE_ALL_TELEMETRY")).then(() => {
      setShowGlobalConfirm(false);
      setGlobalConfirmText("");
    });
  };

  return (
    <div
      style={css(
        "margin-bottom:14px;border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--bg2)",
      )}
    >
      <div
        style={css(
          "display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,280px);gap:20px;align-items:start",
        )}
      >
        <div>
          <div
            style={css(
              "display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;flex-wrap:wrap",
            )}
          >
            <span
              style={css(
                "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)",
              )}
            >
              Filters
            </span>
            {hasActiveFilters(value) && (
              <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--accentLine)")}>
                {filtersLabel(value)}
              </span>
            )}
          </div>

          <div
            style={css(
              "display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-bottom:10px",
            )}
          >
            <FilterSelect
              label="Chapter"
              value={chapterId}
              onChange={setChapterId}
              options={[
                { value: "", label: "All chapters" },
                ...chapters.map((c) => ({
                  value: c.id,
                  label: `Ch ${c.chapter_no}${c.title ? ` · ${c.title}` : ""}`,
                })),
              ]}
            />
            <FilterSelect
              label="Run"
              value={runId}
              onChange={setRunId}
              options={[
                { value: "", label: "All runs" },
                ...runs
                  .filter((r) => r.run_id)
                  .map((r) => ({
                    value: r.run_id!,
                    label: r.started_at
                      ? new Date(r.started_at).toLocaleString([], {
                          month: "numeric",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : r.run_id!.slice(0, 8),
                  })),
              ]}
            />
            <label style={css("display:flex;flex-direction:column;gap:3px")}>
              <span style={css("font-family:var(--mono);font-size:9px;color:var(--dim)")}>Scene</span>
              <input
                type="number"
                min={1}
                value={sceneNo}
                onChange={(e) => setSceneNo(e.target.value)}
                placeholder="Any"
                style={inputStyle}
              />
            </label>
            <label style={css("display:flex;flex-direction:column;gap:3px")}>
              <span style={css("font-family:var(--mono);font-size:9px;color:var(--dim)")}>Stage</span>
              <input
                list="telemetry-stages"
                value={stage}
                onChange={(e) => setStage(e.target.value)}
                placeholder="Any"
                style={inputStyle}
              />
            </label>
            <label style={css("display:flex;flex-direction:column;gap:3px")}>
              <span style={css("font-family:var(--mono);font-size:9px;color:var(--dim)")}>Model</span>
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Any"
                style={inputStyle}
              />
            </label>
          </div>

          <div style={css("display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px")}>
            <Toggle label="Truncated" on={truncated} set={setTruncated} />
            <Toggle label="Errors" on={errorsOnly} set={setErrorsOnly} />
            <Toggle label="Fallbacks" on={fallbacksOnly} set={setFallbacksOnly} />
            <Toggle label="Expensive" on={expensive} set={setExpensive} />
            <Toggle label="Cache miss" on={cacheMiss} set={setCacheMiss} />
          </div>

          <div style={css("display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px")}>
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => applyPreset(p.id)}
                style={css(
                  "height:26px;padding:0 10px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-family:var(--mono);font-size:10.5px;cursor:pointer",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div style={css("display:flex;gap:8px;flex-wrap:wrap")}>
            <button
              type="button"
              onClick={applyForm}
              style={css(
                "height:28px;padding:0 14px;border-radius:8px;border:1px solid var(--accentLine);background:color-mix(in srgb,var(--accentLine) 12%,var(--bg3));color:var(--ink);font-size:12px;cursor:pointer",
              )}
            >
              Apply · view calls
            </button>
            {hasActiveFilters(value) && (
              <button
                type="button"
                onClick={onClear}
                style={css(
                  "height:28px;padding:0 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:12px;cursor:pointer",
                )}
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        <div
          style={css(
            "border-left:1px solid var(--line);padding-left:16px;min-height:100%;display:flex;flex-direction:column;gap:10px",
          )}
        >
          <span
            style={css(
              "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)",
            )}
          >
            Data management
          </span>
          <div style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim);line-height:1.45")}>
            {effectiveRunId ? (
              <>
                Selected run:{" "}
                <span style={{ color: "var(--ink)" }}>{effectiveRunId.slice(0, 8)}…</span>
              </>
            ) : (
              "Select a run to delete one run’s calls."
            )}
          </div>
          <div style={css("display:flex;flex-direction:column;gap:6px")}>
            <button
              type="button"
              disabled={!effectiveRunId || deleteBusy !== null}
              onClick={deleteThisRun}
              style={css(
                `${dangerBtn};opacity:${!effectiveRunId || deleteBusy ? "0.5" : "1"};cursor:${!effectiveRunId || deleteBusy ? "default" : "pointer"}`,
              )}
            >
              {deleteBusy === "run" ? "Deleting…" : "Delete this run"}
            </button>
            <button
              type="button"
              disabled={deleteBusy !== null}
              onClick={clearBook}
              style={css(`${dangerBtn};cursor:${deleteBusy ? "default" : "pointer"}`)}
            >
              {deleteBusy === "book" ? "Clearing…" : "Clear book telemetry"}
            </button>
            {!showGlobalConfirm ? (
              <button
                type="button"
                disabled={deleteBusy !== null}
                onClick={() => setShowGlobalConfirm(true)}
                style={css(`${dangerBtn};cursor:${deleteBusy ? "default" : "pointer"}`)}
              >
                Clear all telemetry…
              </button>
            ) : (
              <div style={css("display:flex;flex-direction:column;gap:6px")}>
                <span style={css("font-size:11px;color:var(--dim)")}>
                  Type <strong style={{ color: "var(--ink)" }}>{GLOBAL_CONFIRM_PHRASE}</strong> to
                  wipe telemetry for all books.
                </span>
                <input
                  value={globalConfirmText}
                  onChange={(e) => setGlobalConfirmText(e.target.value)}
                  placeholder={GLOBAL_CONFIRM_PHRASE}
                  style={inputStyle}
                />
                <div style={css("display:flex;gap:6px")}>
                  <button
                    type="button"
                    disabled={
                      globalConfirmText.trim() !== GLOBAL_CONFIRM_PHRASE || deleteBusy !== null
                    }
                    onClick={clearAll}
                    style={css(`${dangerBtn};cursor:pointer`)}
                  >
                    {deleteBusy === "global" ? "Clearing…" : "Confirm global wipe"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowGlobalConfirm(false);
                      setGlobalConfirmText("");
                    }}
                    style={css(
                      "height:28px;padding:0 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:12px;cursor:pointer",
                    )}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
          {deleteError && (
            <div style={css("font-size:11px;color:var(--bad);line-height:1.4")}>{deleteError}</div>
          )}
        </div>
      </div>

      <datalist id="telemetry-stages">
        {(stageHints ?? []).map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label style={css("display:flex;flex-direction:column;gap:3px")}>
      <span style={css("font-family:var(--mono);font-size:9px;color:var(--dim)")}>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={inputStyle}>
        {options.map((o) => (
          <option key={o.value || "all"} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Toggle({ label, on, set }: { label: string; on: boolean; set: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => set(!on)}
      style={css(
        `height:26px;padding:0 10px;border-radius:7px;border:1px solid ${on ? "var(--accentLine)" : "var(--line)"};background:${on ? "color-mix(in srgb,var(--accentLine) 14%,var(--bg3))" : "var(--bg3)"};color:${on ? "var(--ink)" : "var(--dim)"};font-family:var(--mono);font-size:10.5px;cursor:pointer`,
      )}
    >
      {label}
    </button>
  );
}

export function stageOptionsFromBook(data: BookTelemetryOut | null): string[] {
  if (!data) return [];
  return data.by_stage.map((s) => s.key);
}
