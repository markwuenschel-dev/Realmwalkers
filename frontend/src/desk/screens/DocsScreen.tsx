import { useEffect, useMemo, useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import type { DocDetail, DocMeta } from "../api/types";
import ProseBlocks from "../components/ProseBlocks";

// The canon-doc viewer (Domain B): lists the story bible / planning / style Markdown the author keeps
// on disk and renders the selected one through the shared block/inline renderer. Read-only — these
// files are authored outside the app. Fetches the docs index once, then the body on selection.

const CATEGORIES: { id: string; label: string }[] = [
  { id: "canon", label: "Canon" },
  { id: "planning", label: "Planning" },
  { id: "style", label: "Style" },
];

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

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

  // Word export (MarketMind styling, page-numbered) — docx-js lazy-loaded on click.
  const exportDocx = async () => {
    if (!doc) return;
    const docx = await import("../lib/docx");
    await docx.saveDocx(docx.buildDocDoc(doc.title, doc.content), docx.docxFilename(doc.title));
  };

  if (listErr) {
    return (
      <p style={css("text-align:center;color:var(--bad);font-family:var(--mono);font-size:13px;margin-top:40px")}>
        Couldn't load docs — {listErr}
      </p>
    );
  }
  if (!docs) {
    return (
      <p style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:13px;margin-top:40px")}>
        Loading canon…
      </p>
    );
  }
  if (docs.length === 0) {
    return (
      <p style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:13px;margin-top:40px")}>
        No canon, planning, or style docs found on disk.
      </p>
    );
  }

  return (
    <div style={css("display:flex;gap:26px;align-items:flex-start;max-width:72rem;margin:0 auto")}>
      {/* index: docs grouped by category */}
      <nav style={css("flex:0 0 252px;position:sticky;top:78px;max-height:calc(100vh - 110px);overflow-y:auto;padding-right:4px")}>
        {CATEGORIES.map(({ id, label }) => {
          const items = groups[id] ?? [];
          if (items.length === 0) return null;
          return (
            <div key={id} style={css("margin-bottom:18px")}>
              <div style={css("font-family:var(--mono);font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);margin:0 0 7px 8px")}>
                {label} · {items.length}
              </div>
              {items.map((d) => {
                const active = d.path === sel;
                return (
                  <button
                    key={d.path}
                    onClick={() => setSel(d.path)}
                    title={d.path}
                    style={css(
                      `display:block;width:100%;text-align:left;padding:6px 9px;margin-bottom:1px;border:none;border-radius:7px;cursor:pointer;` +
                        `font-family:var(--ui);font-size:12.5px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;` +
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

      {/* reading panel */}
      <article style={css("flex:1 1 auto;min-width:0;max-width:46rem;padding:6px 4px 80px")}>
        {docErr ? (
          <p style={css("color:var(--bad);font-family:var(--mono);font-size:13px")}>Couldn't load — {docErr}</p>
        ) : !doc ? (
          <p style={css("color:var(--dim);font-family:var(--mono);font-size:13px")}>Loading…</p>
        ) : (
          <>
            <div style={css("display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px")}>
              <div style={css("font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);min-width:0;overflow:hidden;text-overflow:ellipsis")}>
                {doc.path}
              </div>
              <button onClick={exportDocx} title="Download this doc as Word (.docx) — MarketMind styling, page-numbered"
                style={css("flex:none;padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);font-family:var(--ui);font-size:12px;cursor:pointer")}>⬇ Word</button>
            </div>
            <ProseBlocks text={doc.content} proseSize="15.5px" justify={false} />
          </>
        )}
      </article>
    </div>
  );
}
