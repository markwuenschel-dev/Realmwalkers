"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useRouter } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { DESK_ROUTES } from "../routes";

interface Result {
  key: string;
  icon: string;
  label: string;
  sub?: string;
  hint?: string;
  cat: string;
  run: () => void;
}

const snippet = (s: string | null, n = 72) => (s ?? "").replace(/\s+/g, " ").trim().slice(0, n);
const CAP = 8; // max results shown per searchable category

export default function CommandPalette() {
  const desk = useDesk();
  const { openSceneId, setLedgerCat, nextScene, prevScene, togglePalette, closePalette } = desk;
  const router = useRouter();
  const data = useDeskData();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const navTo = useCallback(
    (href: string) => {
      closePalette();
      router.push(href);
    },
    [router, closePalette],
  );

  // Static navigation/actions — generated from the route registry; filtered by the query like
  // everything else. Queue next/prev are actions, not routes.
  const commands: Result[] = useMemo(
    () => [
      ...DESK_ROUTES.map((r) => ({
        key: `go:${r.id}`,
        icon: r.icon,
        label: r.paletteLabel,
        hint: r.shortcut,
        cat: "Go",
        run: () => navTo(r.href),
      })),
      {
        key: "act:next",
        icon: "⤓",
        label: "Next scene in queue",
        hint: "J",
        cat: "Go",
        run: nextScene,
      },
      {
        key: "act:prev",
        icon: "⤒",
        label: "Previous scene in queue",
        hint: "K",
        cat: "Go",
        run: prevScene,
      },
    ],
    [navTo, nextScene, prevScene],
  );

  // Latest version of each (chapter, scene) — what you'd want to jump to.
  const latestScenes = data.latestScenes;
  const chapterNo = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const c of data.chapters) m.set(c.id, c.chapter_no ?? null);
    return m;
  }, [data.chapters]);

  const results: Result[] = useMemo(() => {
    const query = q.trim().toLowerCase();
    const hit = (hay: string) => query === "" || hay.toLowerCase().includes(query);
    const out: Result[] = [];

    for (const c of commands) if (hit(`${c.label} ${c.hint ?? ""}`)) out.push(c);
    if (query === "") return out; // empty query: just the launcher commands

    let n = 0;
    for (const s of latestScenes) {
      if (n >= CAP) break;
      const ch = chapterNo.get(s.chapter_id);
      const label = `Ch ${ch ?? "?"} · Scene ${s.scene_no}`;
      if (hit(`chapter ${ch} scene ${s.scene_no} ${s.status} ${s.prose ?? ""}`)) {
        out.push({
          key: `sc:${s.id}`,
          icon: "❖",
          label,
          sub: `${s.status.replace(/_/g, " ")} · ${snippet(s.prose) || "—"}`,
          cat: "Scenes",
          run: () => openSceneId(s.id),
        });
        n++;
      }
    }
    n = 0;
    for (const c of data.chapters) {
      if (n >= CAP) break;
      if (hit(`chapter ${c.chapter_no} ${c.title ?? ""} ${c.pov}`)) {
        out.push({
          key: `ch:${c.id}`,
          icon: "▦",
          label: `Chapter ${c.chapter_no}${c.title ? ` — ${c.title}` : ""}`,
          sub: `POV · ${c.pov}`,
          cat: "Chapters",
          run: () => navTo("/chapters"),
        });
        n++;
      }
    }
    n = 0;
    for (const e of data.canon) {
      if (n >= CAP) break;
      if (hit(`${e.kind ?? ""} ${e.name ?? ""} ${e.body ?? ""}`)) {
        const kind = e.kind ?? "other";
        out.push({
          key: `cn:${e.id}`,
          icon: "◍",
          label: e.name ?? "(unnamed)",
          sub: `${kind} · ${snippet(e.body ?? null) || "—"}`,
          cat: "Canon",
          run: () => {
            setLedgerCat(kind === "character" ? "characters" : `canon:${kind}`);
            navTo("/ledger");
          },
        });
        n++;
      }
    }
    n = 0;
    for (const c of data.characters) {
      if (n >= CAP) break;
      if (hit(`${c.character} ${JSON.stringify(c.stats)} ${c.body ?? ""}`)) {
        out.push({
          key: `cs:${c.character}`,
          icon: "◍",
          label: c.character,
          sub: `${c.is_pov ? "POV" : "character"}${c.body ? ` · ${snippet(c.body, 50)}` : ""}`,
          cat: "Characters",
          run: () => {
            setLedgerCat("characters");
            navTo("/ledger");
          },
        });
        n++;
      }
    }
    return out;
  }, [
    q,
    commands,
    latestScenes,
    chapterNo,
    data.chapters,
    data.canon,
    data.characters,
    navTo,
    openSceneId,
    setLedgerCat,
  ]);

  // Keep the selection in range as results change; scroll it into view.
  const active = results.length ? Math.min(sel, results.length - 1) : 0;
  useEffect(() => {
    setSel(0);
  }, [q]);
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-i="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      results[active]?.run();
    }
  };

  const stop = (e: MouseEvent) => e.stopPropagation();

  // Category dividers: the cat of the result above changed.
  let lastCat = "";

  return (
    <div
      onClick={togglePalette}
      style={css(
        "position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.62);display:flex;align-items:flex-start;justify-content:center;padding-top:13vh;animation:fadeIn .14s ease both",
      )}
    >
      <div
        onClick={stop}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        style={css(
          "width:min(600px,92vw);background:var(--bg2);border:1px solid var(--line);border-radius:13px;box-shadow:0 30px 80px rgba(0,0,0,.5);overflow:hidden;animation:fadeUp .18s ease both",
        )}
      >
        <div
          style={css(
            "display:flex;align-items:center;gap:11px;padding:15px 18px;border-bottom:1px solid var(--line)",
          )}
        >
          <span style={css("color:var(--dim)")}>⌕</span>
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            aria-label="Search scenes, chapters, canon, characters, or a screen"
            placeholder="Search scenes, chapters, canon, characters — or a screen…"
            style={css("flex:1;background:transparent;border:none;color:var(--ink);font-size:15px")}
          />
          <span
            style={css(
              "font-family:var(--mono);font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:1px 6px",
            )}
          >
            esc
          </span>
        </div>
        <div ref={listRef} style={css("padding:8px;max-height:56vh;overflow-y:auto")}>
          {results.length === 0 && (
            <div
              style={css(
                "padding:26px;text-align:center;font-family:var(--mono);font-size:12.5px;color:var(--dim)",
              )}
            >
              No matches for “{q}”.
            </div>
          )}
          {results.map((r, i) => {
            const showCat = r.cat !== lastCat;
            lastCat = r.cat;
            const isSel = i === active;
            return (
              <div key={r.key}>
                {showCat && q.trim() !== "" && (
                  <div
                    style={css(
                      "font-family:var(--mono);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);padding:9px 11px 4px",
                    )}
                  >
                    {r.cat}
                  </div>
                )}
                <button
                  data-i={i}
                  onClick={() => r.run()}
                  onMouseMove={() => setSel(i)}
                  style={css(
                    `display:flex;align-items:center;gap:13px;width:100%;padding:10px 13px;border:none;border-radius:8px;cursor:pointer;text-align:left;font-size:13.5px;background:${isSel ? "var(--bg3)" : "transparent"};color:var(--ink)`,
                  )}
                >
                  <span style={css("width:22px;text-align:center;color:var(--accent);flex:none")}>
                    {r.icon}
                  </span>
                  <span style={css("flex:1;min-width:0")}>
                    <span
                      style={css(
                        "display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis",
                      )}
                    >
                      {r.label}
                    </span>
                    {r.sub && (
                      <span
                        style={css(
                          "display:block;font-family:var(--mono);font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px",
                        )}
                      >
                        {r.sub}
                      </span>
                    )}
                  </span>
                  {r.hint && (
                    <span
                      style={css(
                        "font-family:var(--mono);font-size:10.5px;color:var(--dim);flex:none",
                      )}
                    >
                      {r.hint}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
