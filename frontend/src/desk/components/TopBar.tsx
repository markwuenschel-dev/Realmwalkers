"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { DraftPill } from "./DraftActivity";
import { DESK_ROUTES, activeRouteId } from "../routes";
import type { Screen } from "../types";

// Atelier variant toggle — one identity, two moods. Ink (dark, night study) / Vellum (light,
// parchment page). Inline SVGs so there is no icon dependency.
function SunIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}

export default function TopBar() {
  const { isDark, setTheme, togglePalette } = useDesk();
  const { pending, books, bookId } = useDeskData();
  const pathname = usePathname();
  const activeId = activeRouteId(pathname);
  const badgeFor = (id: Screen): string | null =>
    id === "inbox" && pending.length ? String(pending.length) : null;
  // Initial of the active book (the desk is book-scoped); fall back to the Atelier mark.
  const bookInitial =
    (books.find((b) => b.id === bookId)?.title ?? "A").trim().charAt(0).toUpperCase() || "A";

  return (
    <header
      className="no-print"
      style={css(
        "position:sticky;top:0;z-index:40;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 18px;height:60px;background:var(--bg2b);border-bottom:1px solid var(--line)",
      )}
    >
      <div style={css("display:flex;align-items:center;gap:20px;min-width:0")}>
        <Link
          href="/inbox"
          style={css(
            "display:flex;align-items:center;gap:11px;cursor:pointer;flex:none;text-decoration:none",
          )}
        >
          <div
            style={css(
              "width:26px;height:26px;border-radius:6px;border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:15px;color:var(--accent);background:var(--accentSoft)",
            )}
          >
            A
          </div>
          <div
            style={css(
              "font-family:var(--display);font-size:15.5px;letter-spacing:.02em;color:var(--ink);white-space:nowrap",
            )}
          >
            Atelier <span style={css("color:var(--dim)")}>· Writers' Desk</span>
          </div>
        </Link>
        <nav style={css("display:flex;gap:2px")}>
          {DESK_ROUTES.filter((n) => n.nav).map((n) => {
            const active = activeId === n.id;
            const badge = badgeFor(n.id);
            return (
              <Link
                key={n.id}
                href={n.href}
                aria-current={active ? "page" : undefined}
                style={css(
                  `display:inline-flex;align-items:center;padding:7px 10px;border:none;border-radius:8px;cursor:pointer;font-family:var(--ui);font-size:13px;white-space:nowrap;text-decoration:none;background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-weight:${active ? "500" : "400"}`,
                )}
              >
                {n.label}
                {badge && (
                  <span
                    style={css(
                      "margin-left:7px;font-family:var(--mono);font-size:10px;padding:1px 6px;border-radius:999px;background:var(--accent);color:var(--onAccent)",
                    )}
                  >
                    {badge}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <DraftPill />
      </div>
      <div style={css("display:flex;align-items:center;gap:10px;flex:none")}>
        <button
          className="dk-btn"
          onClick={() => setTheme(isDark ? "light" : "dark")}
          aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
          title={isDark ? "Vellum — parchment page" : "Ink — night study"}
          style={css(
            "display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:999px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);cursor:pointer",
          )}
        >
          {isDark ? <SunIcon /> : <MoonIcon />}
        </button>
        <button
          onClick={togglePalette}
          aria-label="Open command palette"
          style={css(
            "display:flex;align-items:center;gap:8px;height:34px;padding:0 12px;border-radius:999px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:12.5px;cursor:pointer;font-family:var(--ui)",
          )}
        >
          Search
          <span
            style={css(
              "font-family:var(--mono);font-size:11px;border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:var(--dim)",
            )}
          >
            ⌘K
          </span>
        </button>
        <div
          title={books.find((b) => b.id === bookId)?.title ?? undefined}
          style={css(
            "width:30px;height:30px;border-radius:50%;background:var(--accentSoft);border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:13px;color:var(--accent)",
          )}
        >
          {bookInitial}
        </div>
      </div>
    </header>
  );
}
