"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { wordCount } from "../lib/format";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import { useTabLoadTiming } from "../lib/useTabLoadTiming";
import ProseBlocks from "../components/ProseBlocks";
import { beautify } from "../lib/beautify";
import { Button, Chip } from "../components/ui";
import type { ManuscriptOut, ManuscriptPart, ManuscriptVolume } from "../api/types";
import { chapterLabel, partKindWord, toRoman } from "../manuscript/labels";
import { bookNumberLabel } from "../manuscript/metadata";

// Shunn standard manuscript format counts ~250 words to a page.
const WORDS_PER_PAGE = 250;

// Reading layouts: a comfortable single measure, a wider one, or a true two-column book spread.
// Two-column flows each chapter's prose into balanced columns (chapter header + end span full width).
type Layout = "page" | "wide" | "columns";
const LAYOUTS: { id: Layout; label: string }[] = [
  { id: "page", label: "Page" },
  { id: "wide", label: "Wide" },
  { id: "columns", label: "Two-column" },
];
const WIDTH: Record<Layout, string> = { page: "40rem", wide: "54rem", columns: "66rem" };

// Two compile sources: the approved manuscript (canon), or a working draft that pulls in EVERY scene's
// current prose regardless of review status — a read-only preview that never accepts anything.
type Source = "approved" | "draft";

export default function ManuscriptScreen() {
  const {
    manuscript,
    chapters: allChapters,
    latestScenes,
    jobs,
    clearFailed,
    clearDraftScenes,
    refreshManuscript,
  } = useDeskData();
  const [layout, setLayout] = useState<Layout>("wide");
  const [source, setSource] = useState<Source>("approved");
  const [clearFailedBusy, setClearFailedBusy] = useState(false);
  const [clearDraftBusy, setClearDraftBusy] = useState(false);
  // Author name for the Shunn submission header/byline — persisted (shared with every other export
  // surface) so it isn't re-typed each export.
  const [author, saveAuthor] = useAuthorName();

  // The approved manuscript is intentionally dropped from the post-action refresh (heaviest payload; a
  // scene decision elsewhere doesn't need it recompiled). Ask for a compile when this screen opens —
  // the provider serves the cached one with ZERO network while it's still fresh (nothing that touches
  // the compile happened since), and only refetches when a scene action / chapter edit marked it
  // stale. The cached compile stays on screen during that background refetch either way.
  useEffect(() => {
    void refreshManuscript();
  }, [refreshManuscript]);

  // Tab-switch cost, visible in the console: with a warm cache this logs ~0ms (no fetch at all).
  useTabLoadTiming("manuscript", manuscript != null);

  // Draft compile: assemble each scene's current (latest-version) prose into manuscript form, whatever
  // its status — built entirely client-side from data already loaded, so viewing/exporting it never
  // touches scene status. Nothing is "accepted" by reading the draft.
  const draftManuscript = useMemo<ManuscriptOut | null>(() => {
    if (!manuscript) return null;
    const chs = [...allChapters]
      .sort((a, b) => (a.position ?? a.chapter_no ?? 0) - (b.position ?? b.chapter_no ?? 0))
      .map((ch) => ({
        position: ch.position,
        chapter_no: ch.chapter_no,
        title: ch.title,
        pov: ch.pov,
        kind: ch.kind,
        epigraph: ch.epigraph,
        scenes: latestScenes
          .filter((s) => s.chapter_id === ch.id)
          .sort((a, b) => a.scene_no - b.scene_no)
          .map((s) => ({ scene_no: s.scene_no, prose: s.prose })),
      }));
    return { ...manuscript, chapters: chs };
  }, [manuscript, allChapters, latestScenes]);

  const isDraft = source === "draft";
  const active = isDraft ? draftManuscript : manuscript;
  const chapters = active?.chapters ?? [];
  const hasProse = chapters.some((c) => c.scenes.some((s) => (s.prose ?? "").trim()));
  // Not-yet-approved scenes the draft compile pulls in (shown as a toolbar hint).
  const draftExtra = latestScenes.filter(
    (s) => (s.prose ?? "").trim() && s.status !== "approved",
  ).length;

  const isColumns = layout === "columns";
  const proseSize = isColumns ? "16.5px" : "18.5px";
  const bodyStyle = isColumns ? "column-count:2;column-gap:2.8rem" : "";

  const totalWords = chapters
    .flatMap((c) => c.scenes)
    .reduce((acc, s) => acc + wordCount(s.prose), 0);
  const pages = totalWords > 0 ? Math.max(1, Math.ceil(totalWords / WORDS_PER_PAGE)) : 0;

  const titleStem =
    (active?.title || "manuscript").replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || "manuscript";
  const draftSuffix = isDraft ? "_draft" : "";

  const compiled = (): ManuscriptOut | null => (active ? { ...active, chapters } : null);

  const modeSubtitle = isDraft
    ? "working draft — all scenes, including unapproved"
    : "the approved manuscript, in reading order";

  // Reader label + part lookup now come from the shared export contract — the on-screen reader, the
  // three emitters, and preflight all agree on how a Prologue or a Part is titled.
  const partById = useMemo(() => new Map((active?.parts ?? []).map((p) => [p.id, p])), [active]);

  // De-hardcoded book identity line (was a literal "Book One"): series + spelled-out book number from
  // the book's metadata. Empty for a book with no series identity.
  const bookLine = [active?.series?.toUpperCase(), bookNumberLabel(active?.book_no ?? undefined)]
    .filter(Boolean)
    .join(" · ");

  const volumeById = useMemo(
    () => new Map((active?.volumes ?? []).map((v) => [v.id, v])),
    [active],
  );

  // Reading-order render list: Volume + Part dividers interleaved with their chapters (only prose-bearing
  // chapters render). Mirrors the spine the emitters walk, so the on-screen reader and the exports agree
  // on where a Volume/Part opens.
  type RenderItem =
    | { kind: "volume"; volume: ManuscriptVolume }
    | { kind: "part"; part: ManuscriptPart }
    | { kind: "chapter"; ch: ManuscriptOut["chapters"][number] };
  const renderItems = useMemo<RenderItem[]>(() => {
    const items: RenderItem[] = [];
    let lastPart: string | null = null;
    let lastVolume: string | null = null;
    for (const ch of active?.chapters ?? []) {
      if (!ch.scenes.some((s) => (s.prose ?? "").trim())) continue;
      const pid = ch.part_id ?? null;
      const part = pid ? partById.get(pid) : undefined;
      const vid = part?.volume_id ?? null;
      if (vid && vid !== lastVolume) {
        const volume = volumeById.get(vid);
        if (volume) {
          items.push({ kind: "volume", volume });
          lastPart = null; // force the part divider to re-emit under the new volume
        }
      }
      lastVolume = vid;
      if (pid && pid !== lastPart && part) items.push({ kind: "part", part });
      lastPart = pid;
      items.push({ kind: "chapter", ch });
    }
    return items;
  }, [active, partById, volumeById]);

  const exportMarkdown = async () => {
    const ms = compiled();
    if (!ms) return;
    const { exportAndSave } = await import("../manuscript/exportActions");
    await exportAndSave(ms, {
      preset: "editorial_review",
      filenameStem: titleStem + draftSuffix,
      draft: isDraft,
      archival: true,
    });
  };

  const exportDocx = async () => {
    const ms = compiled();
    if (!ms) return;
    const { exportAndSave } = await import("../manuscript/exportActions");
    await exportAndSave(ms, {
      preset: "reader_proof",
      filenameStem: titleStem + draftSuffix,
      draft: isDraft,
      renderSubtitle: modeSubtitle,
      archival: true,
    });
  };

  const exportShunn = async () => {
    const ms = compiled();
    if (!ms) return;
    const name = resolveAuthorName(author, saveAuthor);
    if (!name) return;
    const { exportAndSave } = await import("../manuscript/exportActions");
    // Shunn is submission-safe: preflight can BLOCK on errors. Try once honoring the gate; if blocked,
    // show what failed and let the human force an explicit override.
    const result = await exportAndSave(ms, {
      preset: "submission_shunn",
      filenameStem: `${titleStem}_shunn${draftSuffix}`,
      author: name,
      draft: isDraft,
      archival: true,
    });
    if (result.preflight.blocked) {
      const errs = result.preflight.issues.filter((i) => i.severity === "error");
      const detail = errs
        .slice(0, 8)
        .map((i) => `• ${i.location?.label ? `${i.location.label}: ` : ""}${i.message}`)
        .join("\n");
      const more = errs.length > 8 ? `\n…and ${errs.length - 8} more.` : "";
      if (
        confirm(
          `Shunn preflight found ${errs.length} submission-blocking issue${errs.length === 1 ? "" : "s"}:\n\n${detail}${more}\n\nExport anyway?`,
        )
      ) {
        await exportAndSave(ms, {
          preset: "submission_shunn",
          filenameStem: `${titleStem}_shunn${draftSuffix}`,
          author: name,
          draft: isDraft,
          override: true,
          archival: true,
        });
      }
    }
  };

  return (
    <div>
      {/* toolbar: page estimate + export (left) · reading-layout control (right) */}
      <div
        className="no-print"
        style={css(
          "display:flex;align-items:center;justify-content:space-between;gap:12px;max-width:66rem;margin:0 auto 6px;padding:0 4px",
        )}
      >
        <div style={css("display:flex;align-items:center;gap:12px")}>
          {/* compile source — approved canon vs. a full working draft (all scenes, read-only) */}
          <div style={css("display:flex;gap:5px")} title="What to compile">
            {(
              [
                ["approved", "Approved"],
                ["draft", "Draft"],
              ] as const
            ).map(([id, label]) => {
              const on = source === id;
              return (
                <Button
                  key={id}
                  size="sm"
                  variant={on ? "primary" : "ghost"}
                  onClick={() => setSource(id)}
                >
                  {label}
                </Button>
              );
            })}
          </div>
          {totalWords > 0 && (
            <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
              ≈ {pages.toLocaleString()} manuscript page{pages === 1 ? "" : "s"} ·{" "}
              {totalWords.toLocaleString()} words
              <span style={css("opacity:.6")}> · Shunn 250 wpp</span>
            </span>
          )}
          {isDraft && (
            <>
              <Button
                size="sm"
                disabled={clearFailedBusy || clearDraftBusy || jobs.failed <= 0}
                onClick={async () => {
                  setClearFailedBusy(true);
                  try {
                    await clearFailed();
                  } finally {
                    setClearFailedBusy(false);
                  }
                }}
                title="Remove failed draft jobs for this book"
              >
                {clearFailedBusy ? "Clearing…" : "Clear failed jobs"}
              </Button>
              <Button
                size="sm"
                style="color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line))"
                disabled={clearFailedBusy || clearDraftBusy}
                onClick={async () => {
                  const n = draftExtra;
                  if (
                    !confirm(
                      n > 0
                        ? `Clear ${n} unapproved draft scene${n === 1 ? "" : "s"}? Approved prose is kept.`
                        : "Clear all non-approved draft scenes? Approved prose is kept.",
                    )
                  )
                    return;
                  setClearDraftBusy(true);
                  try {
                    await clearDraftScenes();
                  } finally {
                    setClearDraftBusy(false);
                  }
                }}
                title="Delete all non-approved scenes so the draft compile resets"
              >
                {clearDraftBusy ? "Clearing…" : "Clear draft scenes"}
              </Button>
            </>
          )}
          {isDraft && draftExtra > 0 && (
            <Chip label={`incl. ${draftExtra} unapproved`} tone="warn" />
          )}
          <Button
            size="sm"
            onClick={() => void exportMarkdown()}
            disabled={!hasProse}
            title="Semantic Markdown — YAML front matter, preserved @interface blocks for agents"
          >
            Export Markdown
          </Button>
          <Button
            size="sm"
            onClick={() => void exportDocx()}
            disabled={!hasProse}
            title="Reader DOCX — styled book format with LitRPG interface panels"
          >
            Export Reader DOCX
          </Button>
          <Button
            size="sm"
            onClick={() => void exportShunn()}
            disabled={!hasProse}
            title="Shunn DOCX — plain submission format for agents/editors"
          >
            Export Shunn DOCX
          </Button>
          <input
            value={author}
            onChange={(e) => saveAuthor(e.target.value)}
            placeholder="author name (Shunn)"
            title="Used in the Shunn submission header & byline"
            style={css(
              "width:130px;padding:5px 9px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);font-family:var(--ui);font-size:12px",
            )}
          />
        </div>
        <div style={css("display:flex;gap:5px")} title="Reading layout">
          {LAYOUTS.map((l) => {
            const active = layout === l.id;
            return (
              <Button
                key={l.id}
                size="sm"
                variant={active ? "primary" : "ghost"}
                onClick={() => setLayout(l.id)}
              >
                {l.label}
              </Button>
            );
          })}
        </div>
      </div>

      <article
        className="ms-print"
        style={css(`max-width:${WIDTH[layout]};margin:0 auto;padding:20px 0 60px`)}
      >
        <div
          className="ms-title"
          style={css(
            "text-align:center;margin-bottom:64px;padding-bottom:40px;border-bottom:1px solid var(--line)",
          )}
        >
          {bookLine ? (
            <div
              style={css(
                "font-family:var(--mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);margin-bottom:20px",
              )}
            >
              {bookLine}
            </div>
          ) : null}
          <h1
            style={css(
              "margin:0 0 18px;font-family:var(--display);font-weight:500;font-size:46px;letter-spacing:-.01em;color:var(--ink)",
            )}
          >
            {manuscript?.title ?? "—"}
          </h1>
          <div
            style={css(
              "display:inline-block;border-top:1px solid var(--accentLine);padding-top:14px;font-family:var(--prose);font-style:italic;font-size:16px;color:var(--dim)",
            )}
          >
            {isDraft
              ? "working draft — all scenes, including unapproved"
              : "the approved manuscript, in reading order"}
          </div>
        </div>

        {!hasProse && (
          <p
            style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:13px")}
          >
            {isDraft
              ? "No scenes drafted yet — outline a chapter and draft scenes from the inbox."
              : "No approved scenes yet — approve a scene in the inbox, or switch to Draft to compile everything."}
          </p>
        )}

        {renderItems.map((item) => {
          if (item.kind === "volume") {
            const volume = item.volume;
            return (
              <div
                key={`vol-${volume.id}`}
                className="ms-volume"
                style={css("text-align:center;margin:24px 0 64px;padding-bottom:32px")}
              >
                <div
                  style={css(
                    "font-family:var(--mono);font-size:13px;letter-spacing:.34em;text-transform:uppercase;color:var(--accent);margin-bottom:14px",
                  )}
                >
                  Volume {toRoman(volume.volume_no)}
                </div>
                <div
                  style={css(
                    "font-family:var(--display);font-weight:500;font-size:42px;color:var(--ink)",
                  )}
                >
                  {volume.title}
                </div>
                {volume.subtitle ? (
                  <div
                    style={css(
                      "font-family:var(--display);font-style:italic;font-size:17px;color:var(--dim);margin-top:8px",
                    )}
                  >
                    {volume.subtitle}
                  </div>
                ) : null}
              </div>
            );
          }
          if (item.kind === "part") {
            const part = item.part;
            return (
              <div
                key={`part-${part.id}`}
                className="ms-part"
                style={css(
                  "text-align:center;margin:16px 0 60px;padding-bottom:28px;border-bottom:1px solid var(--line)",
                )}
              >
                <div
                  style={css(
                    "font-family:var(--mono);font-size:12px;letter-spacing:.28em;text-transform:uppercase;color:var(--accent);margin-bottom:12px",
                  )}
                >
                  {partKindWord(part.kind)} {toRoman(part.part_no)}
                </div>
                <div
                  style={css(
                    "font-family:var(--display);font-weight:500;font-size:34px;color:var(--ink)",
                  )}
                >
                  {part.title}
                </div>
                {part.subtitle ? (
                  <div
                    style={css(
                      "font-family:var(--display);font-style:italic;font-size:16px;color:var(--dim);margin-top:8px",
                    )}
                  >
                    {part.subtitle}
                  </div>
                ) : null}
              </div>
            );
          }
          const ch = item.ch;
          const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
          if (scenes.length === 0) return null;
          return (
            <section
              key={`ch-${ch.chapter_no}`}
              className="ms-chapter"
              style={css("margin-bottom:54px")}
            >
              {/* chapter header — spans the full measure, even in two-column */}
              <div style={css("text-align:center;margin-bottom:30px")}>
                <div
                  style={css(
                    "font-family:var(--display);font-variant:small-caps;font-size:16px;letter-spacing:.14em;color:var(--accent);margin-bottom:9px",
                  )}
                >
                  {chapterLabel(ch)}
                </div>
                <h2
                  style={css(
                    "margin:0;font-family:var(--display);font-weight:500;font-size:25px;color:var(--ink)",
                  )}
                >
                  {ch.title ? ch.title : null}
                  <span
                    style={css(
                      "display:block;font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);font-weight:400;margin-top:6px",
                    )}
                  >
                    POV · {ch.pov}
                  </span>
                </h2>
              </div>

              {(ch.epigraph ?? "").trim() ? (
                <div
                  style={css(
                    "text-align:center;max-width:34rem;margin:-8px auto 30px;font-family:var(--display);font-style:italic;font-size:15px;line-height:1.65;color:var(--dim)",
                  )}
                >
                  {ch.epigraph}
                </div>
              ) : null}

              {/* chapter body — single measure or balanced columns */}
              <div style={css(bodyStyle)}>
                {scenes.map((sc, si) => (
                  <Fragment key={sc.scene_no}>
                    {si > 0 && (
                      // scene break: end of one scene / start of the next
                      <div
                        aria-hidden
                        className="ms-scenebreak"
                        style={css(
                          "text-align:center;color:var(--dim);font-size:15px;letter-spacing:.6em;margin:1.5em 0;break-inside:avoid",
                        )}
                      >
                        ⁂
                      </div>
                    )}
                    <ProseBlocks text={beautify(sc.prose ?? "")} proseSize={proseSize} />
                  </Fragment>
                ))}
              </div>

              {/* chapter end — distinct from a scene break, spans the full measure */}
              <div
                style={css(
                  "display:flex;align-items:center;justify-content:center;gap:16px;margin:40px 0 7px",
                )}
              >
                <span style={css("height:1px;width:56px;background:var(--line)")} />
                <span style={css("color:var(--accent);font-size:16px;letter-spacing:.4em")}>✦</span>
                <span style={css("height:1px;width:56px;background:var(--line)")} />
              </div>
              <div
                style={css(
                  "text-align:center;font-family:var(--mono);font-size:9.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim)",
                )}
              >
                End of {chapterLabel(ch)}
              </div>
            </section>
          );
        })}
      </article>
    </div>
  );
}
