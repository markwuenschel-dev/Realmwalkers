import { Fragment, useState } from "react";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { wordCount } from "../lib/format";
import ProseBlocks from "../components/ProseBlocks";

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

export default function ManuscriptScreen() {
  const { manuscript } = useDeskData();
  const [layout, setLayout] = useState<Layout>("wide");
  const chapters = manuscript?.chapters ?? [];
  const hasProse = chapters.some((c) => c.scenes.some((s) => (s.prose ?? "").trim()));

  const isColumns = layout === "columns";
  const proseSize = isColumns ? "16.5px" : "18.5px";
  const bodyStyle = isColumns ? "column-count:2;column-gap:2.8rem" : "";

  const totalWords = chapters.flatMap((c) => c.scenes).reduce((acc, s) => acc + wordCount(s.prose), 0);
  const pages = Math.max(1, Math.ceil(totalWords / WORDS_PER_PAGE));

  // Assemble the approved manuscript as Markdown and download it client-side (no deps, no server call).
  const exportMarkdown = () => {
    const title = manuscript?.title ?? "Untitled";
    const lines: string[] = [`# ${title}`, "", "_Book One — the approved manuscript, in reading order_", ""];
    for (const ch of chapters) {
      const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
      if (scenes.length === 0) continue;
      lines.push("", `## Chapter ${ch.chapter_no}${ch.title ? ` — ${ch.title}` : ""}`, "", `*POV — ${ch.pov}*`, "");
      scenes.forEach((sc, si) => {
        if (si > 0) lines.push("", "\\* \\* \\*", ""); // scene break
        lines.push((sc.prose ?? "").trim());
      });
      lines.push("", `— End of Chapter ${ch.chapter_no} —`, "");
    }
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || "manuscript"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Word export (book typography, page-numbered) — docx-js is lazy-loaded so it stays out of the
  // main bundle and only downloads when you click.
  const exportDocx = async () => {
    if (!manuscript) return;
    const docx = await import("../lib/docx");
    await docx.saveDocx(docx.buildManuscriptDoc(manuscript), docx.docxFilename(manuscript.title || "manuscript"));
  };

  return (
    <div>
      {/* toolbar: page estimate + export (left) · reading-layout control (right) */}
      <div className="no-print" style={css("display:flex;align-items:center;justify-content:space-between;gap:12px;max-width:66rem;margin:0 auto 6px;padding:0 4px")}>
        <div style={css("display:flex;align-items:center;gap:12px")}>
          {hasProse && (
            <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
              ≈ {pages.toLocaleString()} manuscript page{pages === 1 ? "" : "s"} · {totalWords.toLocaleString()} words
              <span style={css("opacity:.6")}> · Shunn 250 wpp</span>
            </span>
          )}
          <button onClick={exportMarkdown} disabled={!hasProse} title="Download the approved manuscript as Markdown"
            style={css(`padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${hasProse ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${hasProse ? "pointer" : "default"}`)}>⬇ Markdown</button>
          <button onClick={exportDocx} disabled={!hasProse} title="Download as Word (.docx) — book format, page-numbered"
            style={css(`padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${hasProse ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${hasProse ? "pointer" : "default"}`)}>⬇ Word</button>
          <button onClick={() => window.print()} disabled={!hasProse} title="Print, or save as a PDF — chapters break to new pages (enable the print dialog's headers/footers for page numbers)"
            style={css(`padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:${hasProse ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:12px;cursor:${hasProse ? "pointer" : "default"}`)}>⎙ Print / PDF</button>
        </div>
        <div style={css("display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px")}>
          {LAYOUTS.map((l) => {
            const active = layout === l.id;
            return (
              <button key={l.id} onClick={() => setLayout(l.id)}
                style={css(`padding:5px 13px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`)}>{l.label}</button>
            );
          })}
        </div>
      </div>

      <article className="ms-print" style={css(`max-width:${WIDTH[layout]};margin:0 auto;padding:20px 0 60px`)}>
        <div className="ms-title" style={css("text-align:center;margin-bottom:64px;padding-bottom:40px;border-bottom:1px solid var(--line)")}>
          <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);margin-bottom:20px")}>Book One</div>
          <h1 style={css("margin:0 0 14px;font-family:var(--display);font-weight:600;font-size:46px;letter-spacing:.01em;color:var(--ink)")}>{manuscript?.title ?? "—"}</h1>
          <div style={css("font-family:var(--prose);font-style:italic;font-size:16px;color:var(--dim)")}>the approved manuscript, in reading order</div>
        </div>

        {!hasProse && (
          <p style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:13px")}>No approved scenes yet — approve a scene in the inbox and it lands here.</p>
        )}

        {chapters.map((ch) => {
          const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
          if (scenes.length === 0) return null;
          return (
            <section key={ch.chapter_no} className="ms-chapter" style={css("margin-bottom:54px")}>
              {/* chapter header — spans the full measure, even in two-column */}
              <div style={css("text-align:center;margin-bottom:30px")}>
                <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:9px")}>Chapter {ch.chapter_no}</div>
                <h2 style={css("margin:0;font-family:var(--display);font-weight:500;font-size:25px;color:var(--ink)")}>
                  {ch.title ? ch.title : null}
                  <span style={css("display:block;font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);font-weight:400;margin-top:6px")}>POV · {ch.pov}</span>
                </h2>
              </div>

              {/* chapter body — single measure or balanced columns */}
              <div style={css(bodyStyle)}>
                {scenes.map((sc, si) => (
                  <Fragment key={sc.scene_no}>
                    {si > 0 && (
                      // scene break: end of one scene / start of the next
                      <div aria-hidden className="ms-scenebreak" style={css("text-align:center;color:var(--dim);font-size:15px;letter-spacing:.6em;margin:1.5em 0;break-inside:avoid")}>⁂</div>
                    )}
                    <ProseBlocks text={sc.prose ?? ""} proseSize={proseSize} />
                  </Fragment>
                ))}
              </div>

              {/* chapter end — distinct from a scene break, spans the full measure */}
              <div style={css("display:flex;align-items:center;justify-content:center;gap:16px;margin:40px 0 7px")}>
                <span style={css("height:1px;width:56px;background:var(--line)")} />
                <span style={css("color:var(--accent);font-size:16px;letter-spacing:.4em")}>✦</span>
                <span style={css("height:1px;width:56px;background:var(--line)")} />
              </div>
              <div style={css("text-align:center;font-family:var(--mono);font-size:9.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim)")}>End of Chapter {ch.chapter_no}</div>
            </section>
          );
        })}
      </article>
    </div>
  );
}
