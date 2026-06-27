"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { Spinner } from "../components/DraftActivity";
import { TotalsStrip, TotalsTable, fmtTokens } from "../components/Telemetry";
import type { BookTelemetryOut, ChapterRollupOut, TelemetryGroupOut } from "../api/types";

// The global Telemetry tab: persisted LLM-call cost/cache/health for the active book, rolled up so a
// human can compare across chapters, models, and stages (the scene-packet Author/QA, and any later
// instrumented call site). This is the "own tab" cross-chapter view — the per-chapter slice lives
// under the scene packets. Read-only: telemetry is produced by the workers, never edited here.

export default function TelemetryScreen() {
  const { bookId } = useDeskData();
  const [data, setData] = useState<BookTelemetryOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    try {
      setData(await api.bookTelemetry(bookId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [bookId]);

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
