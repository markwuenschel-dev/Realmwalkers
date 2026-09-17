// CoverageBanner — what a read-through actually read. Always shown with a loaded run: a partial run,
// a stopped one, or cross-chapter notes built only from summaries must never pass for a complete
// edit of the book.

import type { ReactNode } from "react";
import type { ReadThroughChapterOut, ReadThroughOut } from "../../api/types";
import { css } from "../../css";
import {
  bookPassLabel,
  chaptersNotReadByBookPass,
  chaptersWithStatus,
  formatCount,
  modelsUsed,
  snapshotDate,
  sortedChapters,
  statusSentence,
} from "../../lib/readThroughMarkdown";
import { Chip, Panel, type ChipTone } from "../ui";

const STATUS_TONE: Record<string, ChipTone> = {
  succeeded: "good",
  partial: "warn",
  stopped: "warn",
  interrupted: "warn",
  failed: "bad",
};

const labels = (chapters: readonly ReadThroughChapterOut[]): string =>
  chapters.map((c) => c.label).join("; ");

function Row({ label, tone, children }: { label: string; tone?: string; children: ReactNode }) {
  return (
    <div
      style={css(
        "display:grid;grid-template-columns:minmax(120px,200px) minmax(0,1fr);gap:12px;align-items:baseline;" +
          "padding:6px 0;border-top:1px solid var(--line)",
      )}
    >
      <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>{label}</span>
      <div style={css(`font-size:13px;line-height:1.5;color:${tone ?? "var(--ink)"}`)}>
        {children}
      </div>
    </div>
  );
}

export default function CoverageBanner({ rt }: { rt: ReadThroughOut }) {
  const chapters = sortedChapters(rt);
  const done = chaptersWithStatus(rt, "done");
  const failed = chaptersWithStatus(rt, "failed");
  const skipped = chaptersWithStatus(rt, "skipped");
  const unreached = chaptersWithStatus(rt, "pending", "running");
  const unreadByBookPass = chaptersNotReadByBookPass(rt);
  const models = modelsUsed(rt);
  const trimmed = chapters.filter((c) => c.notes_dropped > 0 || c.notes_capped);
  const fromDigests = rt.book_pass_status === "done" && rt.book_input_mode === "digests";
  const bookPassTone =
    fromDigests || rt.book_pass_status === "failed"
      ? "var(--warn)"
      : rt.book_pass_status === "done"
        ? undefined
        : "var(--dim)";

  return (
    <Panel
      eyebrow="Coverage — what was read"
      title={statusSentence(rt.status)}
      actions={<Chip label={rt.status} tone={STATUS_TONE[rt.status] ?? "neutral"} />}
    >
      {rt.error && (
        <Row label="Error" tone="var(--bad)">
          {rt.error}
        </Row>
      )}
      <Row label="Chapters read">
        {`${done.length} of ${chapters.length}${done.length > 0 ? ` — ${labels(done)}` : ""}`}
      </Row>
      {failed.length > 0 && (
        <Row label={`Failed (${failed.length})`} tone="var(--bad)">
          {failed.map((c) => (
            <div key={c.id}>{c.error ? `${c.label} — ${c.error}` : c.label}</div>
          ))}
        </Row>
      )}
      {skipped.length > 0 && (
        <Row label={`Skipped (${skipped.length})`} tone="var(--warn)">
          {labels(skipped)}
        </Row>
      )}
      {unreached.length > 0 && (
        <Row label={`Not reached (${unreached.length})`} tone="var(--warn)">
          {labels(unreached)}
        </Row>
      )}
      <Row label="Cross-chapter notes" tone={bookPassTone}>
        {fromDigests ? <strong>{bookPassLabel(rt, "banner")}</strong> : bookPassLabel(rt, "banner")}
      </Row>
      {unreadByBookPass.length > 0 && (
        <Row label="Not read by the cross-chapter pass" tone="var(--warn)">
          {labels(unreadByBookPass)}
        </Row>
      )}
      <Row label="Voice guide" tone={rt.voice_guide_used ? undefined : "var(--warn)"}>
        {rt.voice_guide_used
          ? "Your voice guide was used"
          : "No voice guide was loaded — voice was judged without it"}
      </Row>
      <Row label="Models">{models.length > 0 ? models.join(", ") : "none recorded"}</Row>
      <Row label="Spend">
        {`Attempts ${rt.attempts_used} of ${rt.attempt_allowance} · ${formatCount(rt.tokens_charged)} tokens charged`}
      </Row>
      {rt.accounting_gap && (
        <Row label="Accounting" tone="var(--warn)">
          Some model spend for this run was not recorded
        </Row>
      )}
      <Row label="Snapshot">{`Chapter text as supplied on ${snapshotDate(rt.created_at)}`}</Row>
      {trimmed.length > 0 && (
        <Row label="Notes trimmed">
          {trimmed.map((c) => (
            <div key={c.id}>
              {[
                c.label,
                c.notes_dropped > 0
                  ? `${c.notes_dropped} note${c.notes_dropped === 1 ? "" : "s"} dropped (no quote could be located)`
                  : null,
                c.notes_capped ? "more notes existed than were kept" : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </div>
          ))}
        </Row>
      )}
    </Panel>
  );
}
