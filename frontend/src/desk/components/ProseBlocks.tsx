import { createElement, Fragment } from "react";
import { css } from "../css";
import { parseBlocks, parseInline, type ProseBlock, type Tone } from "../prose";

// Renders a compact Markdown subset as themed blocks: paragraphs, headings, lists,
// blockquote callouts, tables (accent header — the in-app side of the MarketMind
// table style), fenced code, monospace stat windows (the backend's box-drawing art,
// kept aligned), and rules. Inline `code`/**bold**/*em*/links are rendered too. Used
// by the manuscript reading view and the canon-doc viewer; kept generic so the
// print/PDF path reuses the same HTML.

const TONE_COLOR: Record<Tone, string> = {
  note: "var(--accent)",
  info: "var(--info)",
  good: "var(--good)",
  warn: "var(--warn)",
  bad: "var(--bad)",
};

const HSIZE = ["1.7em", "1.4em", "1.2em", "1.05em", "0.95em", "0.9em"];

function interfacePreviewLine(attrs: Record<string, string>): string {
  const role = (attrs.role ?? "interface").toUpperCase();
  const parts = [role];
  if (attrs.creature) parts.push(`CREATURE · ${attrs.creature.toUpperCase()}`);
  if (attrs.domain) parts.push(attrs.domain.toUpperCase());
  return parts.join(" · ");
}

function Inline({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((tok, i) => {
        switch (tok.t) {
          case "code":
            return (
              <code
                key={i}
                style={css(
                  "font-family:var(--mono);font-size:.86em;background:var(--boxbg);" +
                    "border:1px solid var(--line);border-radius:4px;padding:1px 5px",
                )}
              >
                {tok.s}
              </code>
            );
          case "strong":
            return (
              <strong key={i} style={css("font-weight:650;color:var(--ink)")}>
                {tok.s}
              </strong>
            );
          case "em":
            return <em key={i}>{tok.s}</em>;
          case "link":
            return (
              <a
                key={i}
                href={tok.href}
                target="_blank"
                rel="noreferrer"
                style={css("color:var(--accent);text-decoration:underline")}
              >
                {tok.s}
              </a>
            );
          default:
            return <Fragment key={i}>{tok.s}</Fragment>;
        }
      })}
    </>
  );
}

function StatWindow({ lines }: { lines: string[] }) {
  // The box-drawing art carries its own border, so the panel behind it is borderless.
  return (
    <pre
      style={css(
        "font-family:var(--mono);font-size:12.5px;line-height:1.4;white-space:pre;overflow-x:auto;" +
          "display:block;width:max-content;max-width:100%;margin:1.5em auto;padding:14px 18px;" +
          "background:var(--boxbg);border-radius:var(--r);color:var(--ink);break-inside:avoid",
      )}
    >
      {lines.join("\n")}
    </pre>
  );
}

function CodeBlock({ lines }: { lines: string[] }) {
  return (
    <pre
      style={css(
        "font-family:var(--mono);font-size:12.5px;line-height:1.5;white-space:pre;overflow-x:auto;" +
          "margin:1.4em 0;padding:13px 16px;background:var(--boxbg);border:1px solid var(--line);" +
          "border-radius:var(--r);color:var(--ink);break-inside:avoid",
      )}
    >
      {lines.join("\n")}
    </pre>
  );
}

function Callout({ block }: { block: Extract<ProseBlock, { kind: "callout" }> }) {
  const c = TONE_COLOR[block.tone];
  return (
    <div
      style={css(
        `margin:1.2em 0;padding:11px 15px;border-left:3px solid ${c};background:var(--bg2);` +
          "border-radius:0 var(--r) var(--r) 0;break-inside:avoid",
      )}
    >
      {block.title && (
        <div
          style={css(
            `font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;` +
              `color:${c};margin-bottom:6px`,
          )}
        >
          {block.title}
        </div>
      )}
      {block.lines
        .filter((ln) => ln.trim())
        .map((ln, j) => (
          <p
            key={j}
            style={css("margin:0 0 .45em;font-size:.95em;line-height:1.6;color:var(--ink)")}
          >
            <Inline text={ln} />
          </p>
        ))}
    </div>
  );
}

function Table({ block }: { block: Extract<ProseBlock, { kind: "table" }> }) {
  const { head, rows, align } = block;
  const al = (i: number) => align[i] ?? "left";
  return (
    <div style={css("overflow-x:auto;margin:1.5em 0")}>
      <table
        style={css(
          "border-collapse:collapse;width:100%;font-family:var(--ui);font-size:14px;break-inside:avoid",
        )}
      >
        <thead>
          <tr>
            {head.map((h, i) => (
              <th
                key={i}
                style={css(
                  `text-align:${al(i)};padding:8px 12px;background:var(--accent);color:var(--onAccent);` +
                    "font-weight:600;border:1px solid var(--line)",
                )}
              >
                <Inline text={h} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} style={css(ri % 2 ? "background:var(--bg2)" : "background:transparent")}>
              {r.map((cell, ci) => (
                <td
                  key={ci}
                  style={css(
                    `text-align:${al(ci)};padding:7px 12px;border:1px solid var(--line);color:var(--ink)`,
                  )}
                >
                  <Inline text={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ProseBlocks({
  text,
  proseSize = "18px",
  justify = true,
}: {
  text: string;
  proseSize?: string;
  justify?: boolean;
}) {
  const pStyle = css(
    `font-family:var(--prose);font-size:${proseSize};line-height:1.9;color:var(--ink);` +
      `margin:0 0 1.15em;${justify ? "text-align:justify;hyphens:auto" : ""}`,
  );
  const listStyle = css("margin:.7em 0;padding-left:1.5em;line-height:1.7;color:var(--ink)");
  const liStyle = css("margin:.25em 0");

  return (
    <>
      {parseBlocks(text).map((b, i) => {
        switch (b.kind) {
          case "heading":
            return createElement(
              `h${b.level}`,
              {
                key: i,
                style: css(
                  `font-family:var(--display);font-weight:600;color:var(--ink);line-height:1.25;` +
                    `margin:1.5em 0 .55em;font-size:${HSIZE[b.level - 1]}`,
                ),
              },
              <Inline text={b.text} />,
            );
          case "ul":
            return (
              <ul key={i} style={listStyle}>
                {b.items.map((it, j) => (
                  <li key={j} style={liStyle}>
                    <Inline text={it} />
                  </li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i} style={listStyle}>
                {b.items.map((it, j) => (
                  <li key={j} style={liStyle}>
                    <Inline text={it} />
                  </li>
                ))}
              </ol>
            );
          case "callout":
            return <Callout key={i} block={b} />;
          case "hr":
            return (
              <hr
                key={i}
                style={css("border:none;border-top:1px solid var(--line);margin:1.8em 0")}
              />
            );
          case "stat":
            return <StatWindow key={i} lines={b.lines} />;
      case "code":
        return <CodeBlock key={i} lines={b.lines} />;
      case "interface":
        return <CodeBlock key={i} lines={[interfacePreviewLine(b.attrs), ...b.lines]} />;
      case "table":
            return <Table key={i} block={b} />;
          default:
            return (
              <p key={i} style={pStyle}>
                <Inline text={b.text} />
              </p>
            );
        }
      })}
    </>
  );
}
