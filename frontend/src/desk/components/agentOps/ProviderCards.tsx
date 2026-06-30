"use client";

import { css } from "../../css";
import type { ProviderOut } from "../../api/types";

const DEFAULT_PROVIDERS: ProviderOut[] = [
  { id: "anthropic", label: "Anthropic (Claude)", status: "active", description: "Current agents" },
  { id: "openai_codex", label: "OpenAI Codex", status: "coming_soon" },
  { id: "google_gemini", label: "Google Gemini", status: "coming_soon" },
  { id: "antigravity", label: "Antigravity", status: "coming_soon" },
  { id: "xai_grok", label: "xAI Grok", status: "coming_soon" },
  { id: "cursor", label: "Cursor", status: "coming_soon" },
];

export function ProviderCards({ providers }: { providers?: ProviderOut[] }) {
  const rows = providers?.length ? providers : DEFAULT_PROVIDERS;

  return (
    <div style={css("margin-top:28px;max-width:920px")}>
      <div
        style={css(
          "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:10px",
        )}
      >
        Providers
      </div>
      <p style={css("margin:0 0 14px;font-size:13px;color:var(--dim);line-height:1.45")}>
        External model backends. Only Anthropic is wired today; others are placeholders for future
        routing.
      </p>
      <div
        style={css(
          "display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px",
        )}
      >
        {rows.map((p) => (
          <ProviderCard key={p.id} provider={p} />
        ))}
      </div>
    </div>
  );
}

function ProviderCard({ provider: p }: { provider: ProviderOut }) {
  const active = p.status === "active";

  return (
    <div
      style={css(
        "border:1px solid var(--line);border-radius:10px;padding:14px 16px;background:var(--bg2);display:flex;flex-direction:column;gap:8px;min-height:120px",
      )}
    >
      <div style={css("display:flex;align-items:flex-start;justify-content:space-between;gap:8px")}>
        <div
          style={css("font-family:var(--display);font-size:15px;font-weight:600;color:var(--ink)")}
        >
          {p.label}
        </div>
        <span
          style={css(
            `flex-shrink:0;font-family:var(--mono);font-size:9px;text-transform:uppercase;padding:2px 7px;border-radius:999px;border:1px solid ${active ? "var(--ok)" : "var(--line)"};color:${active ? "var(--ok)" : "var(--dim)"}`,
          )}
        >
          {active ? "Active" : "Coming soon"}
        </span>
      </div>
      {p.description && (
        <div style={css("font-size:12px;color:var(--dim);line-height:1.4")}>{p.description}</div>
      )}
      <button
        type="button"
        disabled={!active}
        style={css(
          `margin-top:auto;height:28px;padding:0 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:${active ? "var(--ink)" : "var(--dim)"};font-size:12px;cursor:${active ? "pointer" : "default"};opacity:${active ? "1" : "0.55"}`,
        )}
      >
        Configure
      </button>
    </div>
  );
}
