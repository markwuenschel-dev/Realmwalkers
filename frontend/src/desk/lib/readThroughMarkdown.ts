// Read-through vocabulary shared by the Notes screen, plus the Markdown export.
//
// The export is the author's portable copy of a run, so it carries the same coverage the screen
// shows — what was read, what failed or was skipped, and whether the cross-chapter notes came from
// the manuscript or only from summaries. A copy that dropped that context would read as a complete
// edit of the book when it may not be one.

import type {
  ReadThroughAnchorOut,
  ReadThroughChapterOut,
  ReadThroughNoteOut,
  ReadThroughOut,
} from "../api/types";

export const ACTIVE_STATUSES: ReadonlySet<string> = new Set(["queued", "running", "stopping"]);
export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "succeeded",
  "partial",
  "failed",
  "stopped",
  "interrupted",
]);
export const isActiveStatus = (s: string): boolean => ACTIVE_STATUSES.has(s);
export const isTerminalStatus = (s: string): boolean => TERMINAL_STATUSES.has(s);

export const PRIORITIES = ["high", "medium", "low"] as const;
const priorityRank = (p: string): number => {
  const i = (PRIORITIES as readonly string[]).indexOf(p);
  return i < 0 ? PRIORITIES.length : i;
};

/** High → medium → low (unknown last), then the order the editor ranked them. */
export const sortNotes = (notes: readonly ReadThroughNoteOut[]): ReadThroughNoteOut[] =>
  [...notes].sort(
    (a, b) => priorityRank(a.priority) - priorityRank(b.priority) || a.position - b.position,
  );

export const capitalize = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const categoryLabel = (c: string): string => capitalize(c.replace(/_/g, " "));

export const sortedChapters = (rt: ReadThroughOut): ReadThroughChapterOut[] =>
  [...rt.chapters].sort((a, b) => a.position - b.position);

export const chaptersWithStatus = (rt: ReadThroughOut, ...statuses: string[]) =>
  sortedChapters(rt).filter((c) => statuses.includes(c.status));

/** Chapters the cross-chapter pass did not include. Only meaningful once that pass has run. */
export const chaptersNotReadByBookPass = (rt: ReadThroughOut): ReadThroughChapterOut[] =>
  rt.book_pass_status === "done"
    ? sortedChapters(rt).filter((c) => !rt.book_chapter_ids.includes(c.id))
    : [];

/** Every model that actually produced output for this run, chapters first, in order. */
export function modelsUsed(rt: ReadThroughOut): string[] {
  const seen = new Set<string>();
  for (const c of sortedChapters(rt)) if (c.model_used) seen.add(c.model_used);
  if (rt.book_model_used) seen.add(rt.book_model_used);
  return [...seen];
}

export const ambiguousCount = (a: ReadThroughAnchorOut): number =>
  a.candidate_count || a.candidates.length;

/** The located quote is the exact source text; otherwise what the model quoted. */
export const anchorQuote = (a: ReadThroughAnchorOut): string =>
  a.state === "located" && a.segments.length > 0
    ? a.segments.map((s) => s.text).join(" … ")
    : a.text_quoted;

export const anchorRoleLabel = (role: string): string =>
  role === "location" ? "Where this applies" : "Evidence";

/** "2026-09-15 14:03 UTC" — timezone-stable, so an export reads the same everywhere. */
export const snapshotDate = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
};

export const formatCount = (n: number): string => n.toLocaleString("en-US");

export function statusSentence(status: string): string {
  switch (status) {
    case "succeeded":
      return "Finished";
    case "partial":
      return "Partial — some chapters were not read";
    case "stopped":
      return "Stopped — finished chapters were kept";
    case "interrupted":
      return "Interrupted — the server lost this run; finished chapters were kept";
    case "failed":
      return "Failed";
    case "queued":
      return "Waiting to start";
    case "running":
      return "Running";
    case "stopping":
      return "Stopping";
    default:
      return capitalize(status);
  }
}

export function bookPassLabel(rt: ReadThroughOut, flavour: "banner" | "markdown"): string {
  const reason = rt.book_pass_error ? ` — ${rt.book_pass_error}` : "";
  switch (rt.book_pass_status) {
    case "done":
      if (rt.book_input_mode === "full_text") return "Cross-chapter notes from full text";
      if (rt.book_input_mode === "digests") {
        return flavour === "markdown"
          ? "Cross-chapter notes from summaries (digests), not the manuscript"
          : "Cross-chapter notes from summaries — not the manuscript";
      }
      return "Cross-chapter notes (input not recorded)";
    case "skipped":
      return `Cross-chapter pass skipped${reason}`;
    case "failed":
      return `Cross-chapter pass failed${reason}`;
    case "pending":
      return "Cross-chapter pass not run yet";
    default:
      return `Cross-chapter pass not run${reason}`;
  }
}

const oneLine = (s: string): string => s.replace(/\s*\r?\n\s*/g, " ").trim();
const labelList = (chapters: readonly ReadThroughChapterOut[]): string =>
  chapters.map((c) => c.label).join("; ");

function anchorLine(
  a: ReadThroughAnchorOut,
  chapterLabel: Map<string, string>,
  showChapter: boolean,
) {
  const quote = `- "${oneLine(anchorQuote(a))}"`;
  // Book-note anchors name their chapter: a quote repeated across chapters arrives as one anchor per
  // chapter, so "appears N times (Chapter 2)" is one line per chapter with the total count.
  const label = showChapter && a.chapter_id ? chapterLabel.get(a.chapter_id) : undefined;
  const where = label ? ` (${label})` : "";
  if (a.state === "located") return `${quote}${where}`;
  if (a.state === "ambiguous") return `${quote} — appears ${ambiguousCount(a)} times${where}`;
  return `${quote} — could not be located${where}`;
}

function noteBlock(
  n: ReadThroughNoteOut,
  chapterLabel: Map<string, string>,
  isBookNote: boolean,
): string[] {
  const out = [
    `### ${capitalize(n.priority)} · ${categoryLabel(n.category)} — ${oneLine(n.title)} _(${n.status})_`,
    "",
    n.observation.trim(),
    "",
    `**Recommendation:** ${n.recommendation.trim()}`,
  ];
  if (isBookNote && n.scope_chapter_ids.length > 0) {
    const scope = n.scope_chapter_ids.map((id) => chapterLabel.get(id) ?? id).join("; ");
    out.push("", `**Across:** ${scope}`);
  }
  if (n.anchors.length > 0) {
    out.push("", `**${anchorRoleLabel(n.anchor_role)}:**`);
    for (const a of n.anchors) out.push(anchorLine(a, chapterLabel, isBookNote));
  }
  out.push("");
  return out;
}

export function readThroughMarkdown(rt: ReadThroughOut): string {
  const chapters = sortedChapters(rt);
  const chapterLabel = new Map(chapters.map((c) => [c.id, c.label]));
  const models = modelsUsed(rt);
  const lines: string[] = [
    `# ${oneLine(rt.title)}`,
    "",
    `- **Status:** ${statusSentence(rt.status)}${rt.error ? ` — ${oneLine(rt.error)}` : ""}`,
    `- **Snapshot:** ${snapshotDate(rt.created_at)}`,
    `- **Models:** ${models.length > 0 ? models.join(", ") : "none recorded"}`,
    `- **Voice guide:** ${rt.voice_guide_used ? "used" : "not loaded"}`,
    "",
    "## Coverage",
    "",
  ];

  const done = chaptersWithStatus(rt, "done");
  const failed = chaptersWithStatus(rt, "failed");
  const skipped = chaptersWithStatus(rt, "skipped");
  const unreached = chaptersWithStatus(rt, "pending", "running");
  lines.push(`- Chapters done (${done.length}): ${labelList(done) || "none"}`);
  if (failed.length > 0) {
    const detail = failed.map((c) => (c.error ? `${c.label} — ${oneLine(c.error)}` : c.label));
    lines.push(`- Chapters failed (${failed.length}): ${detail.join("; ")}`);
  }
  if (skipped.length > 0)
    lines.push(`- Chapters skipped (${skipped.length}): ${labelList(skipped)}`);
  if (unreached.length > 0) {
    lines.push(`- Chapters not reached (${unreached.length}): ${labelList(unreached)}`);
  }
  lines.push(`- Book pass: ${bookPassLabel(rt, "markdown")}`);
  const unread = chaptersNotReadByBookPass(rt);
  if (unread.length > 0) lines.push(`- Chapters the book pass did not read: ${labelList(unread)}`);
  lines.push(
    `- Attempts: ${rt.attempts_used} of ${rt.attempt_allowance} · Tokens charged: ${formatCount(rt.tokens_charged)}`,
  );
  if (rt.accounting_gap) lines.push("- Some model spend for this run was not recorded.");
  for (const c of chapters) {
    const bits: string[] = [];
    if (c.notes_dropped > 0) {
      bits.push(
        `${c.notes_dropped} note${c.notes_dropped === 1 ? "" : "s"} dropped (no quote could be located)`,
      );
    }
    if (c.notes_capped) bits.push("more notes existed than were kept");
    if (bits.length > 0) lines.push(`- ${c.label}: ${bits.join("; ")}`);
  }
  lines.push("");

  const bookNotes = sortNotes(rt.notes.filter((n) => n.chapter_id == null));
  lines.push("## Book notes", "");
  if (bookNotes.length === 0) lines.push("_No cross-chapter notes._", "");
  for (const n of bookNotes) lines.push(...noteBlock(n, chapterLabel, true));

  for (const c of chapters) {
    const notes = sortNotes(rt.notes.filter((n) => n.chapter_id === c.id));
    lines.push(`## ${oneLine(c.label)}`, "");
    if (notes.length === 0) {
      lines.push(c.status === "done" ? "_No notes._" : `_Not read (${c.status})._`, "");
    }
    for (const n of notes) lines.push(...noteBlock(n, chapterLabel, false));
  }

  return `${lines.join("\n").trimEnd()}\n`;
}
