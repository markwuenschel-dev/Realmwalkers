"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { css } from "../../css";
import { api } from "../../api/client";
import { Spinner } from "../DraftActivity";
import { TotalsStrip, TotalsTable, fmtTokens } from "../Telemetry";
import type {
  LlmCallOut,
  RunCompareOut,
  RunTelemetryOut,
  TelemetryGroupOut,
  TelemetryTotals,
} from "../../api/types";
import { groupLabel, statusColor, stageFlags, worstCall, type DrawerNav, type TelemetryDrawerView } from "./types";
import { filtersLabel, filtersToApiOpts, type LlmCallFilters } from "./telemetryFilters";

export function TelemetryDrawer({ nav, bookId }: { nav: DrawerNav; bookId: string }) {
  const view = nav.view;
  if (!view) return null;

  return (
    <>
      <div onClick={nav.close} style={css("position:fixed;inset:0;z-index:85;background:rgba(0,0,0,.45)")} />
      <div
        style={css(
          "position:fixed;top:0;right:0;bottom:0;z-index:86;width:min(520px,96vw);overflow-y:auto;background:var(--bg2);border-left:1px solid var(--line);box-shadow:-12px 0 40px rgba(0,0,0,.35);padding:16px 18px",
        )}
      >
        <DrawerHeader nav={nav} />
        {view.kind === "run" && <RunDetail runId={view.runId} bookId={bookId} nav={nav} />}
        {view.kind === "stage" && (
          <StageDetail stage={view.stage} bookId={view.bookId} runId={view.runId} nav={nav} />
        )}
        {view.kind === "model" && (
          <ModelDetail model={view.model} bookId={view.bookId} runId={view.runId} nav={nav} />
        )}
        {view.kind === "scene" && (
          <SceneDetail runId={view.runId} sceneNo={view.sceneNo} bookId={bookId} nav={nav} />
        )}
        {view.kind === "call" && <CallDetail callId={view.callId} nav={nav} />}
        {view.kind === "compare" && (
          <CompareDetail bookId={view.bookId} runA={view.runA} runB={view.runB} />
        )}
        {view.kind === "filtered" && (
          <FilteredCalls label={view.label} bookId={view.bookId} filters={view.filters} nav={nav} />
        )}
      </div>
    </>
  );
}

function DrawerHeader({ nav }: { nav: DrawerNav }) {
  return (
    <div style={css("display:flex;align-items:center;gap:10px;margin-bottom:14px")}>
      {nav.stack.length > 1 && (
        <button
          type="button"
          onClick={nav.pop}
          style={css(
            "height:28px;padding:0 10px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:12px;cursor:pointer",
          )}
        >
          Back
        </button>
      )}
      <h2 style={css("margin:0;font-size:16px;font-weight:600;color:var(--ink);flex:1")}>
        {groupLabel(nav.view)}
      </h2>
      <button
        type="button"
        onClick={nav.close}
        style={css(
          "height:28px;width:28px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);cursor:pointer",
        )}
      >
        ×
      </button>
    </div>
  );
}

function RunDetail({ runId, bookId, nav }: { runId: string; bookId: string; nav: DrawerNav }) {
  const [data, setData] = useState<RunTelemetryOut | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api
      .runTelemetry(runId)
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [runId]);

  if (err) return <Err msg={err} />;
  if (!data)
    return (
      <SpinnerRow />
    );

  const stamp = data.started_at
    ? `${new Date(data.started_at).toLocaleDateString()} ${new Date(data.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : runId.slice(0, 8);
  const chLabel =
    data.chapter_no != null
      ? `Ch ${data.chapter_no}${data.title ? ` · ${data.title}` : ""}`
      : "";

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
        {stamp}
        {chLabel ? ` · ${chLabel}` : ""}
      </div>
      <TotalsStrip t={data.totals} />
      <Section label="Stages">
        <TotalsTable<TelemetryGroupOut>
          label="Stage"
          rows={data.by_stage}
          nameOf={(r) => r.key.replace(/_/g, " ")}
          onRowClick={(r) => nav.push({ kind: "stage", stage: r.key, bookId, runId })}
        />
      </Section>
      <Section label="Scenes">
        <TotalsTable
          label="Scene"
          rows={data.scenes}
          nameOf={(r) => (
            <span>
              Sc{r.scene_no ?? "—"}{" "}
              <span style={css(`color:${statusColor(r.status)}`)}>({r.status})</span>
            </span>
          )}
          onRowClick={(r) => {
            if (r.scene_no != null) nav.push({ kind: "scene", runId, sceneNo: r.scene_no });
          }}
        />
        {data.scenes.map((s) =>
          s.stage_summary ? (
            <div
              key={String(s.scene_no)}
              style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim);padding:2px 4px")}
            >
              Sc{s.scene_no}: {s.stage_summary}
            </div>
          ) : null,
        )}
      </Section>
      <Section label="Calls">
        <CallList calls={data.calls.slice(0, 30)} nav={nav} />
        {data.calls.length > 30 && (
          <div style={css("font-size:11px;color:var(--dim);margin-top:6px")}>
            Showing 30 of {data.calls.length} calls
          </div>
        )}
      </Section>
    </div>
  );
}

function StageDetail({
  stage,
  bookId,
  runId,
  nav,
}: {
  stage: string;
  bookId: string;
  runId?: string;
  nav: DrawerNav;
}) {
  const [calls, setCalls] = useState<LlmCallOut[] | null>(null);
  const [totals, setTotals] = useState<TelemetryTotals | null>(null);

  useEffect(() => {
    api
      .llmCalls({ book_id: bookId, run_id: runId, stage, limit: 100 })
      .then((d) => {
        setCalls(d.calls);
        if (d.calls.length) {
          const t = aggregateCalls(d.calls);
          setTotals(t);
        }
      })
      .catch(() => setCalls([]));
  }, [bookId, runId, stage]);

  if (!calls) return <SpinnerRow />;
  const worst = worstCall(calls);
  const flags = totals ? stageFlags(totals) : [];

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      {totals && <TotalsStrip t={totals} />}
      {flags.length > 0 && (
        <div style={css("font-size:12px;color:var(--warn, #e8a020)")}>{flags.join(" · ")}</div>
      )}
      {worst && (
        <button
          type="button"
          onClick={() => nav.push({ kind: "call", callId: worst.id })}
          style={css(
            "text-align:left;border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--bg3);cursor:pointer;color:var(--ink);font-size:12px",
          )}
        >
          Worst call: {worst.latency_ms ?? "—"}ms · {fmtTokens(worst.input_tokens)} in
        </button>
      )}
      <CallList calls={calls} nav={nav} />
    </div>
  );
}

function ModelDetail({
  model,
  bookId,
  runId,
  nav,
}: {
  model: string;
  bookId: string;
  runId?: string;
  nav: DrawerNav;
}) {
  const [calls, setCalls] = useState<LlmCallOut[] | null>(null);

  useEffect(() => {
    api
      .llmCalls({ book_id: bookId, run_id: runId, model, limit: 100 })
      .then((d) => setCalls(d.calls))
      .catch(() => setCalls([]));
  }, [bookId, runId, model]);

  if (!calls) return <SpinnerRow />;
  const totals = aggregateCalls(calls);
  const flags = stageFlags(totals);

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      <TotalsStrip t={totals} />
      {flags.length > 0 ? (
        <div style={css("font-size:12px;color:var(--warn, #e8a020)")}>This model shows: {flags.join(", ")}</div>
      ) : (
        <div style={css("font-size:12px;color:var(--ok)")}>No obvious issues for this model in scope.</div>
      )}
      <CallList calls={calls} nav={nav} />
    </div>
  );
}

function SceneDetail({
  runId,
  sceneNo,
  bookId,
  nav,
}: {
  runId: string;
  sceneNo: number;
  bookId: string;
  nav: DrawerNav;
}) {
  const [data, setData] = useState<RunTelemetryOut | null>(null);

  useEffect(() => {
    api.runTelemetry(runId).then(setData).catch(() => setData(null));
  }, [runId]);

  if (!data) return <SpinnerRow />;
  const scene = data.scenes.find((s) => s.scene_no === sceneNo);
  if (!scene) return <Err msg="Scene not found in this run" />;

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      <div style={css(`font-family:var(--mono);font-size:12px;color:${statusColor(scene.status)}`)}>
        Status: {scene.status} · {scene.stage_summary}
      </div>
      <TotalsStrip t={scene} />
      <Section label="Pipeline">
        <div style={css("display:flex;flex-direction:column;gap:4px")}>
          {scene.pipeline.map((step, i) => (
            <button
              key={i}
              type="button"
              onClick={() => nav.push({ kind: "stage", stage: step.stage, bookId, runId })}
              style={css(
                "display:flex;justify-content:space-between;text-align:left;border:1px solid var(--line);border-radius:7px;padding:6px 10px;background:var(--bg3);cursor:pointer;color:var(--ink);font-family:var(--mono);font-size:11px",
              )}
            >
              <span>
                {i + 1}. {step.stage.replace(/_/g, " ")}
              </span>
              <span style={css("color:var(--dim)")}>
                {step.calls} calls
                {step.truncations > 0 ? ` · ${step.truncations} trunc` : ""}
              </span>
            </button>
          ))}
        </div>
      </Section>
      <CallList
        calls={data.calls.filter((c) => c.scene_no === sceneNo)}
        nav={nav}
      />
    </div>
  );
}

function CallDetail({ callId, nav }: { callId: string; nav: DrawerNav }) {
  const router = useRouter();
  const [call, setCall] = useState<LlmCallOut | null>(null);

  useEffect(() => {
    api.llmCall(callId).then(setCall).catch(() => setCall(null));
  }, [callId]);

  if (!call) return <SpinnerRow />;
  const meta = (call.metadata ?? {}) as Record<string, unknown>;
  const sections = meta.context_sections as Record<string, number> | undefined;

  return (
    <div style={css("display:flex;flex-direction:column;gap:10px;font-family:var(--mono);font-size:11.5px")}>
      <Row label="Stage" value={call.stage} />
      <Row label="Model" value={call.model} />
      <Row label="Scene" value={call.scene_no != null ? String(call.scene_no) : "—"} />
      <Row label="Latency" value={call.latency_ms != null ? `${call.latency_ms}ms` : "—"} />
      <Row label="Input" value={fmtTokens(call.input_tokens)} />
      <Row label="Output" value={fmtTokens(call.output_tokens)} />
      <Row label="Cache write" value={fmtTokens(call.cache_creation_tokens)} />
      <Row label="Cache read" value={fmtTokens(call.cache_read_tokens)} />
      <Row label="Truncated" value={call.truncated ? "yes" : "no"} />
      <Row label="Error" value={call.error ?? "—"} color={call.error ? "var(--bad)" : undefined} />
      {typeof meta.max_tokens === "number" && <Row label="Max tokens" value={String(meta.max_tokens)} />}
      {typeof meta.stop_reason === "string" && <Row label="Stop reason" value={meta.stop_reason} />}
      {typeof meta.raw_context_total === "number" && (
        <Row
          label="Raw context"
          value={`${fmtTokens(meta.raw_context_total)} / ${meta.context_window_budget ?? "—"}`}
        />
      )}
      {meta.fallback_attempt === true && <Row label="Fallback" value="yes" color="var(--warn, #e8a020)" />}
      {typeof meta.section_name === "string" && <Row label="Section" value={meta.section_name} />}
      {sections && (
        <div>
          <div style={css("color:var(--dim);font-size:10px;margin-bottom:4px")}>Context sections</div>
          {Object.entries(sections).map(([k, v]) => (
            <Row key={k} label={k} value={fmtTokens(v)} />
          ))}
        </div>
      )}
      <div style={css("display:flex;flex-wrap:gap:8px;margin-top:8px")}>
        {call.links.scene_id && (
          <LinkBtn label="Open scene" onClick={() => router.push(`/scene/${call.links.scene_id}`)} />
        )}
        {call.links.chapter_id && (
          <LinkBtn label="Open packets" onClick={() => router.push("/packets")} />
        )}
        {call.run_id && (
          <LinkBtn
            label="Open run"
            onClick={() => nav.push({ kind: "run", runId: call.run_id! })}
          />
        )}
      </div>
    </div>
  );
}

function CompareDetail({
  bookId,
  runA,
  runB,
}: {
  bookId: string;
  runA: string;
  runB: string;
}) {
  const [data, setData] = useState<RunCompareOut | null>(null);

  useEffect(() => {
    api.compareRuns(bookId, runA, runB).then(setData).catch(() => setData(null));
  }, [bookId, runA, runB]);

  if (!data) return <SpinnerRow />;

  const fmtRun = (r: RunCompareOut["run_a"]) =>
    r.started_at
      ? new Date(r.started_at).toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : r.run_id?.slice(0, 8) ?? "—";

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
        {fmtRun(data.run_a)} vs {fmtRun(data.run_b)}
      </div>
      <CompareRow label="Calls" a={data.run_a.calls} b={data.run_b.calls} />
      <CompareRow label="Input" a={data.run_a.input_tokens} b={data.run_b.input_tokens} fmt />
      <CompareRow label="Output" a={data.run_a.output_tokens} b={data.run_b.output_tokens} fmt />
      <CompareRow
        label="Cache"
        a={Math.round(data.run_a.cache_hit_ratio * 100)}
        b={Math.round(data.run_b.cache_hit_ratio * 100)}
        suffix="%"
      />
      <CompareRow label="Trunc" a={data.run_a.truncations} b={data.run_b.truncations} />
      <Section label="Stage deltas">
        {data.stage_deltas.map((d) => (
          <div
            key={d.stage}
            style={css(
              "font-family:var(--mono);font-size:11px;color:var(--dim);padding:4px 0;border-top:1px solid var(--line)",
            )}
          >
            {d.stage.replace(/_/g, " ")}:{" "}
            {d.input_tokens_delta >= 0 ? "+" : ""}
            {fmtTokens(d.input_tokens_delta)} input · {d.truncations_delta >= 0 ? "+" : ""}
            {d.truncations_delta} trunc
          </div>
        ))}
      </Section>
    </div>
  );
}

function FilteredCalls({
  label,
  bookId,
  filters,
  nav,
}: {
  label: string;
  bookId: string;
  filters: LlmCallFilters;
  nav: DrawerNav;
}) {
  const [calls, setCalls] = useState<LlmCallOut[] | null>(null);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    api
      .llmCalls(filtersToApiOpts(bookId, filters, { limit: 100 }))
      .then((d) => {
        setCalls(d.calls);
        setTotal(d.total);
      })
      .catch(() => setCalls([]));
  }, [bookId, JSON.stringify(filters)]);

  if (!calls) return <SpinnerRow />;
  return (
    <div>
      <div style={css("font-size:12px;color:var(--dim);margin-bottom:10px")}>
        {label || filtersLabel(filters)} · {total} match{total === 1 ? "" : "es"}
      </div>
      <CallList calls={calls} nav={nav} />
      {total > calls.length && (
        <div style={css("font-size:11px;color:var(--dim);margin-top:8px")}>
          Showing {calls.length} of {total} calls
        </div>
      )}
    </div>
  );
}

function CallList({ calls, nav }: { calls: LlmCallOut[]; nav: DrawerNav }) {
  if (!calls.length) {
    return <div style={css("font-size:12px;color:var(--dim)")}>No calls</div>;
  }
  return (
    <div style={css("display:flex;flex-direction:column;gap:4px")}>
      {calls.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => nav.push({ kind: "call", callId: c.id })}
          style={css(
            "text-align:left;border:1px solid var(--line);border-radius:7px;padding:7px 10px;background:var(--bg3);cursor:pointer;color:var(--ink);font-family:var(--mono);font-size:11px",
          )}
        >
          <div>
            {c.stage.replace(/_/g, " ")}
            {c.scene_no != null ? ` · Sc${c.scene_no}` : ""}
          </div>
          <div style={css("color:var(--dim);margin-top:2px")}>
            {fmtTokens(c.input_tokens)} in · {c.latency_ms ?? "—"}ms
            {c.truncated ? " · TRUNC" : ""}
          </div>
        </button>
      ))}
    </div>
  );
}

function aggregateCalls(calls: LlmCallOut[]): TelemetryTotals {
  const input_t = calls.reduce((s, c) => s + c.input_tokens, 0);
  const cc = calls.reduce((s, c) => s + c.cache_creation_tokens, 0);
  const cr = calls.reduce((s, c) => s + c.cache_read_tokens, 0);
  const prompt = input_t + cc + cr;
  const latencies = calls.map((c) => c.latency_ms).filter((x): x is number => x != null);
  return {
    calls: calls.length,
    input_tokens: input_t,
    output_tokens: calls.reduce((s, c) => s + c.output_tokens, 0),
    cache_creation_tokens: cc,
    cache_read_tokens: cr,
    cache_hit_ratio: prompt ? cr / prompt : 0,
    cache_tokens_saved: Math.floor(cr * 0.9),
    truncations: calls.filter((c) => c.truncated).length,
    errors: calls.filter((c) => c.error).length,
    fallbacks: calls.filter((c) => (c.metadata as Record<string, unknown> | null)?.fallback_attempt).length,
    avg_latency_ms: latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : null,
    estimated_cost_usd: calls.reduce((s, c) => s + (c.estimated_cost_usd ?? 0), 0),
    cache_savings_usd: 0,
  };
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div
        style={css(
          "font-family:var(--mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:6px",
        )}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={css("display:flex;justify-content:space-between;gap:12px")}>
      <span style={css("color:var(--dim)")}>{label}</span>
      <span style={css(`color:${color ?? "var(--ink)"}`)}>{value}</span>
    </div>
  );
}

function CompareRow({
  label,
  a,
  b,
  fmt,
  suffix = "",
}: {
  label: string;
  a: number;
  b: number;
  fmt?: boolean;
  suffix?: string;
}) {
  const fa = fmt ? fmtTokens(a) : String(a);
  const fb = fmt ? fmtTokens(b) : String(b);
  return (
    <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px")}>
      <span style={css("color:var(--dim)")}>{label}</span>
      <span style={css("color:var(--ink)")}>
        {fa}
        {suffix} vs {fb}
        {suffix}
      </span>
    </div>
  );
}

function LinkBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={css(
        "height:28px;padding:0 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:11.5px;cursor:pointer",
      )}
    >
      {label}
    </button>
  );
}

function SpinnerRow() {
  return (
    <div style={css("display:flex;align-items:center;gap:8px;color:var(--dim);font-size:12px")}>
      <Spinner size={12} /> loading…
    </div>
  );
}

function Err({ msg }: { msg: string }) {
  return <div style={css("color:var(--bad);font-size:12.5px")}>{msg}</div>;
}

export function useTelemetryDrawer() {
  const [stack, setStack] = useState<TelemetryDrawerView[]>([]);
  const view = stack[stack.length - 1] ?? null;
  const open = useCallback((v: TelemetryDrawerView) => setStack([v]), []);
  const push = useCallback((v: TelemetryDrawerView) => setStack((s) => [...s, v]), []);
  const pop = useCallback(() => setStack((s) => (s.length > 1 ? s.slice(0, -1) : [])), []);
  const close = useCallback(() => setStack([]), []);
  return {
    view,
    nav: { view: view!, push, pop, close, stack } as DrawerNav,
    open,
    close,
    isOpen: stack.length > 0,
  };
}
