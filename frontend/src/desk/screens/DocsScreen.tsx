"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { css } from "../css";
import { api } from "../api/client";
import type { DocDetail, DocMeta } from "../api/types";
import ProseBlocks from "../components/ProseBlocks";
import { Button, Eyebrow, Skeleton } from "../components/ui";

// The canon-doc viewer (Domain B): lists the story bible / planning / style Markdown the author keeps
// on disk and renders the selected one through the shared block/inline renderer. Read-only — these
// files are authored outside the app. Fetches the docs index once, then the body on selection.

const CATEGORIES: { id: string; label: string }[] = [
  { id: "frontmatter", label: "Front matter" },
  { id: "canon", label: "Canon" },
  { id: "planning", label: "Planning" },
  { id: "style", label: "Style" },
  { id: "backmatter", label: "Back matter" },
];

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

// Atelier display-XL screen title + the shared page frame.
const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const WRAP = "width:min(96vw,1800px);margin:0 auto;padding:0 clamp(12px,2vw,32px)";

/** Drop a leading `# Title` line — the backend surfaces it as `doc.title` (which the reading view
 *  renders itself in Newsreader), so leaving it in the body would set the title twice. */
const stripTitleHeading = (text: string): string => {
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const s = lines[i].trim();
    if (!s) continue;
    if (s.startsWith("# ")) return [...lines.slice(0, i), ...lines.slice(i + 1)].join("\n");
    break;
  }
  return text;
};

const wordCount = (text: string): number => (text.trim() ? text.trim().split(/\s+/).length : 0);

export default function DocsScreen() {
  const [docs, setDocs] = useState<DocMeta[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [docErr, setDocErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .docs()
      .then((d) => {
        if (!live) return;
        setDocs(d);
        setSel((cur) => cur ?? d[0]?.path ?? null);
      })
      .catch((e) => live && setListErr(errMsg(e)));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!sel) {
      setDoc(null);
      return;
    }
    let live = true;
    setDoc(null);
    setDocErr(null);
    api
      .doc(sel)
      .then((d) => live && setDoc(d))
      .catch((e) => live && setDocErr(errMsg(e)));
    return () => {
      live = false;
    };
  }, [sel]);

  const groups = useMemo(() => {
    const g: Record<string, DocMeta[]> = {};
    for (const d of docs ?? []) (g[d.category] ??= []).push(d);
    return g;
  }, [docs]);

  const body = useMemo(() => (doc ? stripTitleHeading(doc.content) : ""), [doc]);
  const words = useMemo(() => (doc ? wordCount(doc.content) : 0), [doc]);

  // Word export (MarketMind styling, page-numbered) — docx-js lazy-loaded on click.
  const exportDocx = async () => {
    if (!doc) return;
    const docx = await import("../lib/docx");
    await docx.saveDocx(docx.buildDocDoc(doc.title, doc.content), docx.docxFilename(doc.title));
  };

  if (listErr) {
    return (
      <p
        style={css(
          "text-align:center;color:var(--bad);font-family:var(--mono);font-size:13px;margin-top:40px",
        )}
      >
        Couldn't load docs — {listErr}
      </p>
    );
  }
  if (!docs) {
    return (
      <div style={css(WRAP)}>
        <ScreenHeader />
        <div style={css("display:flex;flex-wrap:wrap;gap:26px;align-items:flex-start")}>
          <div style={css("flex:0 1 252px")}>
            <Skeleton lines={8} />
          </div>
          <div style={css("flex:1 1 700px;max-width:68ch")}>
            <Skeleton lines={14} />
          </div>
        </div>
      </div>
    );
  }
  if (docs.length === 0) {
    return (
      <div style={css(WRAP)}>
        <ScreenHeader />
        <EmptyState>No canon, planning, or style docs found on disk.</EmptyState>
      </div>
    );
  }

  return (
    <div style={css(WRAP)}>
      <ScreenHeader count={docs.length} />

      <div style={css("display:flex;flex-wrap:wrap;gap:26px;align-items:flex-start")}>
        {/* index: docs grouped by category */}
        <nav
          style={css(
            "flex:0 1 252px;position:sticky;top:78px;max-height:calc(100vh - 110px);overflow-y:auto;padding-right:4px",
          )}
        >
          {CATEGORIES.map(({ id, label }) => {
            const items = groups[id] ?? [];
            if (items.length === 0) return null;
            return (
              <div key={id} style={css("margin-bottom:20px")}>
                <Eyebrow style="margin:0 0 8px 10px">
                  {label} · {items.length}
                </Eyebrow>
                {items.map((d) => {
                  const active = d.path === sel;
                  return (
                    <button
                      key={d.path}
                      className="dk-navlink"
                      onClick={() => setSel(d.path)}
                      title={d.path}
                      style={css(
                        `display:block;width:100%;text-align:left;padding:6px 10px;margin-bottom:1px;border:none;border-radius:7px;cursor:pointer;` +
                          `font-family:var(--ui);font-size:13px;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;` +
                          `background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};` +
                          `font-weight:${active ? "500" : "400"}`,
                      )}
                    >
                      {d.title}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </nav>

        {/* reading panel — prose measure ~68ch, generous margins */}
        <article
          style={css(
            "flex:1 1 700px;min-width:min(100%,320px);max-width:none;padding:6px 4px 80px",
          )}
        >
          {docErr ? (
            <p style={css("color:var(--bad);font-family:var(--mono);font-size:13px")}>
              Couldn't load — {docErr}
            </p>
          ) : !sel ? (
            <EmptyState>Select a document</EmptyState>
          ) : !doc ? (
            <div style={css("max-width:68ch")}>
              <Skeleton lines={12} />
            </div>
          ) : (
            <div style={css("max-width:68ch")}>
              <header
                style={css(
                  "margin-bottom:26px;padding-bottom:16px;border-bottom:1px solid var(--line)",
                )}
              >
                <h2
                  style={css(
                    "margin:0 0 10px;font-family:var(--display);font-weight:500;font-size:26px;line-height:34px;letter-spacing:-.01em;color:var(--ink)",
                  )}
                >
                  {doc.title}
                </h2>
                <div
                  style={css(
                    "display:flex;align-items:center;justify-content:space-between;gap:12px",
                  )}
                >
                  <div
                    style={css(
                      "font-family:var(--mono);font-size:10.5px;letter-spacing:.04em;color:var(--dim);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap",
                    )}
                  >
                    {doc.path} · {words.toLocaleString()} words
                  </div>
                  <Button
                    size="sm"
                    onClick={exportDocx}
                    title="Download this doc as Word (.docx) — MarketMind styling, page-numbered"
                  >
                    ⬇ Word
                  </Button>
                </div>
              </header>
              <ProseBlocks text={body} proseSize="17px" justify={false} />
            </div>
          )}
        </article>
      </div>
    </div>
  );
}

function ScreenHeader({ count }: { count?: number }) {
  return (
    <header style={css("margin:0 0 30px")}>
      <Eyebrow style="margin-bottom:6px">
        Library{count != null ? ` · ${count} documents` : ""}
      </Eyebrow>
      <h1 style={css(TITLE_XL)}>Canon Library</h1>
    </header>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div style={css("text-align:center;padding:90px 24px")}>
      <div aria-hidden style={css("font-size:20px;color:var(--accent);margin-bottom:16px")}>
        ✦
      </div>
      <p
        style={css(
          "margin:0;font-family:var(--display);font-style:italic;font-size:18px;line-height:1.6;color:var(--dim)",
        )}
      >
        {children}
      </p>
    </div>
  );
}
