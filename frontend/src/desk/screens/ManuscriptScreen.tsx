"use client";

import { Fragment, useMemo, useState } from "react";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { wordCount } from "../lib/format";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import ProseBlocks from "../components/ProseBlocks";
import type { ManuscriptOut } from "../api/types";

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
  } = useDeskData();
  const [layout, setLayout] = useState<Layout>("wide");
  const [source, setSource] = useState<Source>("approved");
  const [clearFailedBusy, setClearFailedBusy] = useState(false);
  const [clearDraftBusy, setClearDraftBusy] = useState(false);
  // Author name for the Shunn submission header/byline — persisted (shared with every other export
  // surface) so it isn't re-typed each export.
  const [author, saveAuthor] = useAuthorName();

  // Draft compile: assemble each scene's current (latest-version) prose into manuscript form, whatever
  // its status — built entirely client-side from data already loaded, so viewing/exporting it never
  // touches scene status. Nothing is "accepted" by reading the draft.
  const draftManuscript = useMemo<ManuscriptOut | null>(() => {
    if (!manuscript) return null;
    const chs = [...allChapters]
      .sort((a, b) => a.chapter_no - b.chapter_no)
      .map((ch) => ({
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

  // Reader-facing chapter label: non-'chapter' kinds show their own name (Prologue/Interlude/…) with
  // no number; a plain chapter stays "Chapter N".
  const chapterLabel = (ch: { kind?: string | null; chapter_no: number }): string => {
    const named: Record<string, string> = {
      prologue: "Prologue",
      interlude: "Interlude",
      epilogue: "Epilogue",
      front_matter: "Front Matter",
      back_matter: "Back Matter",
    };
    return ch.kind && ch.kind !== "chapter" ? (named[ch.kind] ?? ch.kind) : `Chapter ${ch.chapter_no}`;
  };

  const exportMarkdown = async () => {
    const ms = compiled();
    if (!ms) return;
    const exp = await import("../lib/docx");
    exp.saveMarkdown(
      exp.buildManuscriptMarkdown(ms, { draft: isDraft }),
      exp.markdownFilename(titleStem + draftSuffix),
    );
  };

  const exportDocx = async () => {
    const ms = compiled();
    if (!ms) return;
    const docx = await import("../lib/docx");
    await docx.saveDocx(
      docx.buildManuscriptDoc(
        ms,
        isDraft ? "working draft — all scenes, including unapproved" : undefined,
      ),
      docx.docxFilename(titleStem + draftSuffix),
    );
  };

  const exportShunn = async () => {
    const ms = compiled();
    if (!ms) return;
    const name = resolveAuthorName(author, saveAuthor);
    if (!name) return;
    const docx = await import("../lib/docx");
    await docx.saveDocx(
      docx.buildShunnDoc(ms, name, totalWords),
      docx.docxFilename(`${titleStem}_shunn${draftSuffix}`),
    );
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
          <div
            style={css(
              "display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px",
            )}
            title="What to compile"
          >
            {(
              [
                ["approved", "Approved"],
                ["draft", "Draft"],
              ] as const
            ).map(([id, label]) => {
              const on = source === id;
              return (
                <button
                  key={id}
                  onClick={() => setSource(id)}
                  style={css(
                    `padding:5px 12px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12px;background:${on ? "var(--accent)" : "transparent"};color:${on ? "var(--onAccent)" : "var(--dim)"};font-weight:${on ? "600" : "400"}`,
                  )}
                >
                  {label}
                </button>
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
              <button
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
                style={css(
                  `padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${jobs.failed > 0 ? "var(--dim)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${jobs.failed > 0 && !clearFailedBusy ? "pointer" : "default"};opacity:${jobs.failed > 0 ? 1 : 0.45}`,
                )}
              >
                {clearFailedBusy ? "Clearing…" : "Clear failed jobs"}
              </button>
              <button
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
                style={css(
                  `padding:5px 12px;border-radius:7px;border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn) 8%,var(--bg2));color:var(--warn);font-family:var(--ui);font-size:12px;cursor:${clearDraftBusy ? "default" : "pointer"}`,
                )}
              >
                {clearDraftBusy ? "Clearing…" : "Clear draft scenes"}
              </button>
            </>
          )}
          {isDraft && draftExtra > 0 && (
            <span
              style={css(
                "font-family:var(--mono);font-size:11px;color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent);border:1px solid color-mix(in srgb,var(--warn) 38%,transparent);border-radius:999px;padding:2px 9px",
              )}
            >
              incl. {draftExtra} unapproved
            </span>
          )}
          <button
            onClick={exportMarkdown}
            disabled={!hasProse}
            title="Semantic Markdown — YAML front matter, preserved @interface blocks for agents"
            style={css(
              `padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${hasProse ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${hasProse ? "pointer" : "default"}`,
            )}
          >
            Export Markdown
          </button>
          <button
            onClick={exportDocx}
            disabled={!hasProse}
            title="Reader DOCX — styled book format with LitRPG interface panels"
            style={css(
              `padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${hasProse ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${hasProse ? "pointer" : "default"}`,
            )}
          >
            Export Reader DOCX
          </button>
          <button
            onClick={exportShunn}
            disabled={!hasProse}
            title="Shunn DOCX — plain submission format for agents/editors"
            style={css(
              `padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${hasProse ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${hasProse ? "pointer" : "default"}`,
            )}
          >
            Export Shunn DOCX
          </button>
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
        <div
          style={css(
            "display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px",
          )}
        >
          {LAYOUTS.map((l) => {
            const active = layout === l.id;
            return (
              <button
                key={l.id}
                onClick={() => setLayout(l.id)}
                style={css(
                  `padding:5px 13px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`,
                )}
              >
                {l.label}
              </button>
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
          <div
            style={css(
              "font-family:var(--mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);margin-bottom:20px",
            )}
          >
            Book One
          </div>
          <h1
            style={css(
              "margin:0 0 14px;font-family:var(--display);font-weight:600;font-size:46px;letter-spacing:.01em;color:var(--ink)",
            )}
          >
            {manuscript?.title ?? "—"}
          </h1>
          <div
            style={css(
              "font-family:var(--prose);font-style:italic;font-size:16px;color:var(--dim)",
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

        {chapters.map((ch) => {
          const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
          if (scenes.length === 0) return null;
          return (
            <section key={ch.chapter_no} className="ms-chapter" style={css("margin-bottom:54px")}>
              {/* chapter header — spans the full measure, even in two-column */}
              <div style={css("text-align:center;margin-bottom:30px")}>
                <div
                  style={css(
                    "font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:9px",
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
                    <ProseBlocks text={sc.prose ?? ""} proseSize={proseSize} />
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
