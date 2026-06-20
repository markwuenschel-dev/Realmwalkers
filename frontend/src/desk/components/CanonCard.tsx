import { css } from "../css";

// The hover popover shown over a name (entity) or a flagged span (conflict) in the prose. Built from
// spans (not divs) because it lives inside a <p>, exactly as the prototype does it.
export interface CardModel {
  title: string;
  subtitle: string;
  rows: { k: string; v: string }[];
  hasFlag: boolean;
  open: boolean;
  resolved: boolean;
  flagProse: string;
  flagLedger: string;
  resolvedLabel: string;
  keepProse: () => void;
  keepLedger: () => void;
}

const keepBtn =
  "flex:1;padding:6px;border-radius:6px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:10.5px;cursor:pointer;font-family:var(--ui)";

export default function CanonCard({ card }: { card: CardModel }) {
  return (
    <span style={css("position:absolute;left:0;top:calc(100% + 8px);width:246px;background:var(--bg2);border:1px solid var(--line);border-radius:11px;box-shadow:var(--shadow);padding:13px 14px;z-index:60;white-space:normal;text-align:left;line-height:1.45;cursor:default;font-family:var(--ui)")}>
      <span style={css("display:block;font-family:var(--display);font-size:15px;color:var(--ink)")}>{card.title}</span>
      <span style={css("display:block;font-family:var(--mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin:3px 0 8px")}>{card.subtitle}</span>
      {card.rows.map((r, i) => (
        <span key={i} style={css("display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:4px 0;border-top:1px solid var(--hairline)")}>
          <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim);flex:none")}>{r.k}</span>
          <span style={css("color:var(--ink);text-align:right;white-space:nowrap")}>{r.v}</span>
        </span>
      ))}
      {card.hasFlag && (
        <span style={css("display:block;margin-top:9px;padding-top:9px;border-top:1px solid var(--line)")}>
          <span style={css("display:flex;align-items:center;gap:6px;margin-bottom:7px")}>
            <span style={css("width:6px;height:6px;border-radius:50%;background:var(--bad)")} />
            <span style={css("font-family:var(--mono);font-size:9px;letter-spacing:.06em;text-transform:uppercase;color:var(--bad)")}>continuity conflict</span>
          </span>
          <span style={css("display:flex;gap:6px;margin-bottom:8px")}>
            <span style={css("flex:1;padding:5px 8px;border-radius:6px;background:var(--bg3);border:1px solid var(--line)")}>
              <span style={css("display:block;font-family:var(--mono);font-size:8.5px;text-transform:uppercase;color:var(--dim)")}>prose</span>
              <span style={css("font-family:var(--mono);font-size:12px;color:var(--ink)")}>{card.flagProse}</span>
            </span>
            <span style={css("flex:1;padding:5px 8px;border-radius:6px;background:var(--bg3);border:1px solid var(--line)")}>
              <span style={css("display:block;font-family:var(--mono);font-size:8.5px;text-transform:uppercase;color:var(--dim)")}>ledger</span>
              <span style={css("font-family:var(--mono);font-size:12px;color:var(--ink)")}>{card.flagLedger}</span>
            </span>
          </span>
          {card.open && (
            <span style={css("display:flex;gap:6px")}>
              <button onClick={card.keepProse} style={css(keepBtn)}>Keep prose</button>
              <button onClick={card.keepLedger} style={css(keepBtn)}>Keep ledger</button>
            </span>
          )}
          {card.resolved && (
            <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--good)")}>✓ {card.resolvedLabel}</span>
          )}
        </span>
      )}
    </span>
  );
}
