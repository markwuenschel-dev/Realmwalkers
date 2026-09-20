// NotesPane — a list of read-through notes grouped by priority, with their anchors and status buttons.

import type {
  ReadThroughAnchorOut,
  ReadThroughNoteOut,
  ReadThroughProseSuggestionOut,
} from "../../api/types";
import { css } from "../../css";
import { anchorHighlightChapter, anchorKey } from "../../lib/anchorSpans";
import {
  PRIORITIES,
  ambiguousCount,
  anchorQuote,
  anchorRoleLabel,
  capitalize,
  categoryLabel,
} from "../../lib/readThroughMarkdown";
import { Button, Chip, Eyebrow, type ChipTone } from "../ui";

export type NoteStatus = "open" | "done" | "dismissed";

const PRIORITY_TONE: Record<string, ChipTone> = { high: "warn", medium: "info", low: "neutral" };

const MONO = "font-family:var(--mono);font-size:11px;color:var(--dim)";
const QUOTE = "font-family:var(--prose,var(--ui));font-size:13px;line-height:1.5";
const BODY = "font-size:13.5px;line-height:1.55;color:var(--ink)";

const clip = (s: string, n = 160): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function AnchorItem({
  anchor,
  active,
  highlightable,
  chapter,
  onSelect,
}: {
  anchor: ReadThroughAnchorOut;
  active: boolean;
  /** Whether the text pane can draw this anchor (anchorHighlightChapter). */
  highlightable: boolean;
  /** The anchor's chapter label — given for book notes, whose anchors span chapters. */
  chapter: string | null;
  onSelect: () => void;
}) {
  const quote = anchorQuote(anchor);
  const row = css("display:flex;align-items:baseline;gap:6px;flex-wrap:wrap");
  const where = chapter ? <span style={css(MONO)}>{chapter}</span> : null;
  const plainQuote = (
    <span style={css(`${QUOTE};color:var(--dim);font-style:italic`)}>{`“${clip(quote)}”`}</span>
  );
  const ambiguous = anchor.state === "ambiguous";

  if (highlightable) {
    const tone = ambiguous ? "--warn" : "--info";
    const count = ambiguousCount(anchor);
    const times = `appears ${count} times`;
    // Only the saved placements are drawn (the server keeps at most 5 per chapter), while the count is the
    // true total — so the chip must never claim more highlights than the text pane can show.
    const drawn = (anchor.candidates ?? []).filter((c) => c.length > 0).length;
    const s = drawn === 1 ? "" : "s";
    const ambiguousLabel = chapter
      ? `${times} · ${chapter}`
      : drawn < count
        ? `${times} · ${drawn} highlighted`
        : times;
    const ambiguousTitle = chapter
      ? `Highlights ${drawn} placement${s} in ${chapter}; the quote appears ${count} times in all. None was chosen.`
      : drawn < count
        ? `Only ${drawn} of ${count} placements were saved, so only those are highlighted. None was chosen.`
        : "Every placement is highlighted; none was chosen";
    return (
      <div style={row}>
        <button
          type="button"
          aria-pressed={active}
          title={quote}
          onClick={onSelect}
          style={css(
            `${QUOTE};text-align:left;cursor:pointer;color:var(--ink);border:none;border-radius:3px;padding:1px 5px;` +
              `background:color-mix(in srgb,var(${tone}) ${active ? 22 : 8}%,transparent);` +
              `border-bottom:1.5px ${ambiguous ? "dashed" : "solid"} var(${tone})`,
          )}
        >
          {`“${clip(quote)}”`}
        </button>
        {ambiguous ? (
          <Chip
            size="sm"
            tone="warn"
            // A book-note quote repeated across chapters arrives as one anchor per chapter, so the
            // chip names which chapter this one highlights; the count is the total across chapters.
            label={ambiguousLabel}
            title={ambiguousTitle}
          />
        ) : (
          where
        )}
      </div>
    );
  }

  if (ambiguous) {
    // No chapter or no saved placements (rows written before per-chapter anchors): nothing is drawn,
    // so no click is offered and no highlight is claimed.
    return (
      <div style={row}>
        {plainQuote}
        <Chip
          size="sm"
          tone="neutral"
          label={`appears ${ambiguousCount(anchor)} times · not highlighted`}
          title="This quote appears in more than one place, but its placements were not saved, so it is not highlighted"
        />
        {where}
      </div>
    );
  }

  if (anchor.state === "located") {
    return (
      <div style={row}>
        {plainQuote}
        <Chip
          size="sm"
          tone="neutral"
          label="not highlighted"
          title="This quote's position was not saved, so it is not highlighted"
        />
        {where}
      </div>
    );
  }

  return (
    <div style={row}>
      {plainQuote}
      <Chip
        size="sm"
        tone="neutral"
        label="couldn't be located"
        title="This quote was not found in the chapter text"
      />
      {where}
    </div>
  );
}

const MODE_LABEL: Record<string, string> = {
  replace: "replaces",
  insert_before: "goes before",
  insert_after: "goes after",
};

/** Suggested prose for one note. Read-only on purpose: the snapshot is immutable and has no link to
 *  the live chapter, so there is no "apply" this could honestly offer — the author copies it out. */
function SuggestionPanel({ suggestion }: { suggestion: ReadThroughProseSuggestionOut }) {
  if (suggestion.suggestions.length === 0) {
    return (
      <p style={css("margin:10px 0 0;font-size:12.5px;color:var(--dim)")}>
        Nothing came back that quoted the chapter accurately
        {suggestion.fabricated_dropped > 0
          ? ` — ${suggestion.fabricated_dropped} attempt${suggestion.fabricated_dropped === 1 ? "" : "s"} cited text that isn't there and were dropped.`
          : "."}
      </p>
    );
  }
  return (
    <div
      style={css(
        "margin-top:10px;border-left:2px solid var(--accent);padding:8px 0 2px 10px;display:flex;flex-direction:column;gap:12px",
      )}
    >
      {suggestion.suggestions.map((v, i) => (
        <div key={`${v.mode}-${i}`}>
          <div style={css(MONO)}>
            {`${MODE_LABEL[v.mode] ?? v.mode} “${clip(v.anchor_quote, 60)}”`}
          </div>
          <p style={css(`${QUOTE};white-space:pre-wrap;margin:5px 0 0`)}>{v.prose}</p>
          {v.why && <p style={css("margin:5px 0 0;font-size:12.5px;color:var(--dim)")}>{v.why}</p>}
        </div>
      ))}
      <p style={css("margin:0;font-size:11.5px;color:var(--dim)")}>
        {`${suggestion.model} · nothing saved — copy what you want`}
        {suggestion.standards_missing.length > 0
          ? ` · written without ${suggestion.standards_missing.join(", ")}`
          : ""}
        {suggestion.canon_sources.length > 0
          ? ` · canon: ${suggestion.canon_sources.slice(0, 3).join("; ")}`
          : " · no canon matched"}
        {suggestion.telemetry_recorded ? "" : " · cost not recorded"}
      </p>
    </div>
  );
}

function NoteCard({
  note,
  chapterLabel,
  bookNote,
  activeAnchorId,
  onAnchor,
  onStatus,
  onSuggest,
  suggestion,
  suggesting,
  busy,
  error,
}: {
  note: ReadThroughNoteOut;
  chapterLabel: ReadonlyMap<string, string>;
  bookNote: boolean;
  activeAnchorId: string | null;
  onAnchor: (note: ReadThroughNoteOut, index: number) => void;
  onStatus: (note: ReadThroughNoteOut, status: NoteStatus) => void;
  onSuggest: (note: ReadThroughNoteOut) => void;
  suggestion: ReadThroughProseSuggestionOut | undefined;
  suggesting: boolean;
  busy: boolean;
  error: string | undefined;
}) {
  const holdsActive = note.anchors.some((_, i) => anchorKey(note.id, i) === activeAnchorId);
  // The server refuses a note it cannot quote from, so gate the control rather than letting the
  // author spend a call to be told no.
  const hasAnchor = note.anchors.some((a) => a.state === "located" || a.state === "ambiguous");
  const dismissed = note.status === "dismissed";
  return (
    <article
      data-note-id={note.id}
      style={css(
        `border:1px solid ${holdsActive ? "var(--accent)" : "var(--line)"};border-radius:var(--r);padding:12px 14px;` +
          `background:${dismissed ? "transparent" : "var(--bg2)"};opacity:${dismissed ? ".6" : "1"}`,
      )}
    >
      <div style={css("display:flex;gap:6px;flex-wrap:wrap;align-items:center")}>
        <Chip size="sm" label={note.priority} tone={PRIORITY_TONE[note.priority] ?? "neutral"} />
        <Chip size="sm" label={categoryLabel(note.category)} tone="accent2" />
        {note.status !== "open" && (
          <Chip size="sm" label={note.status} tone={note.status === "done" ? "good" : "neutral"} />
        )}
      </div>
      <h3 style={css("margin:8px 0 0;font-size:15px;font-weight:600;color:var(--ink)")}>
        {note.title}
      </h3>
      <p style={css(`${BODY};margin:6px 0 0`)}>{note.observation}</p>
      <Eyebrow style="margin-top:10px">Recommendation</Eyebrow>
      <p style={css(`${BODY};margin:3px 0 0`)}>{note.recommendation}</p>
      {bookNote && note.scope_chapter_ids.length > 0 && (
        <p style={css(`${MONO};margin:8px 0 0`)}>
          {`Across: ${note.scope_chapter_ids.map((id) => chapterLabel.get(id) ?? id).join("; ")}`}
        </p>
      )}
      {note.anchors.length > 0 && (
        <>
          <Eyebrow style="margin-top:10px">{anchorRoleLabel(note.anchor_role)}</Eyebrow>
          <ul
            style={css(
              "list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:5px",
            )}
          >
            {note.anchors.map((a, i) => (
              <li key={i}>
                <AnchorItem
                  anchor={a}
                  active={anchorKey(note.id, i) === activeAnchorId}
                  highlightable={anchorHighlightChapter(note, a) !== null}
                  chapter={
                    bookNote && a.chapter_id ? (chapterLabel.get(a.chapter_id) ?? null) : null
                  }
                  onSelect={() => onAnchor(note, i)}
                />
              </li>
            ))}
          </ul>
        </>
      )}
      <div style={css("display:flex;gap:6px;margin-top:10px")}>
        {note.status === "open" ? (
          <>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => onStatus(note, "done")}
            >
              Done
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => onStatus(note, "dismissed")}
            >
              Dismiss
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onStatus(note, "open")}>
            Reopen
          </Button>
        )}
        {/* A real paid call, and nothing is stored — say so on the control rather than in a doc. */}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy || suggesting || !hasAnchor}
          title={
            hasAnchor
              ? "Write prose answering this note, in your voice and against canon. Costs a model call; nothing is saved."
              : "This note has no anchor in the text, so there is no passage to write against."
          }
          onClick={() => onSuggest(note)}
        >
          {suggesting ? "Writing…" : "Suggest prose"}
        </Button>
      </div>
      {suggestion && <SuggestionPanel suggestion={suggestion} />}
      {error && <p style={css("margin:6px 0 0;font-size:12.5px;color:var(--bad)")}>{error}</p>}
    </article>
  );
}

export default function NotesPane({
  notes,
  chapterLabel,
  bookNotes = false,
  activeAnchorId,
  onAnchor,
  onStatus,
  onSuggest,
  suggestions,
  suggesting,
  busy,
  errors,
  empty,
}: {
  /** Already filtered and sorted (sortNotes). */
  notes: readonly ReadThroughNoteOut[];
  chapterLabel: ReadonlyMap<string, string>;
  bookNotes?: boolean;
  activeAnchorId: string | null;
  onAnchor: (note: ReadThroughNoteOut, index: number) => void;
  onStatus: (note: ReadThroughNoteOut, status: NoteStatus) => void;
  onSuggest: (note: ReadThroughNoteOut) => void;
  suggestions: Readonly<Record<string, ReadThroughProseSuggestionOut>>;
  suggesting: Readonly<Record<string, boolean>>;
  busy: Readonly<Record<string, boolean>>;
  errors: Readonly<Record<string, string>>;
  empty: string;
}) {
  if (notes.length === 0) {
    return <p style={css("margin:0;color:var(--dim);font-size:13.5px")}>{empty}</p>;
  }
  const known = PRIORITIES as readonly string[];
  const groups: [string, ReadThroughNoteOut[]][] = [
    ...PRIORITIES.map((p): [string, ReadThroughNoteOut[]] => [
      p,
      notes.filter((n) => n.priority === p),
    ]),
    ["other", notes.filter((n) => !known.includes(n.priority))],
  ];
  return (
    <div style={css("display:flex;flex-direction:column;gap:16px")}>
      {groups
        .filter(([, group]) => group.length > 0)
        .map(([priority, group]) => (
          <section key={priority}>
            <Eyebrow>{`${capitalize(priority)} priority · ${group.length}`}</Eyebrow>
            <div style={css("display:flex;flex-direction:column;gap:10px;margin-top:8px")}>
              {group.map((n) => (
                <NoteCard
                  key={n.id}
                  note={n}
                  chapterLabel={chapterLabel}
                  bookNote={bookNotes}
                  activeAnchorId={activeAnchorId}
                  onAnchor={onAnchor}
                  onStatus={onStatus}
                  onSuggest={onSuggest}
                  suggestion={suggestions[n.id]}
                  suggesting={!!suggesting[n.id]}
                  busy={!!busy[n.id]}
                  error={errors[n.id]}
                />
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}
