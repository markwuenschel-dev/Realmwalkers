import { css } from "../css";
import { useDesk } from "../state";
import { STATS } from "../data";

export default function InboxScreen() {
  const { t, go } = useDesk();

  const board = [
    {
      title: "Drafting", color: t.info, count: 1, cards: [
        { no: 9, version: 1, title: "The stairwell descent", words: "—", tag: "writing…", onClick: () => {},
          style: "background:var(--bg2);border:1px dashed var(--line);border-radius:10px;padding:13px 14px",
          tagStyle: "color:var(--info);display:inline-flex;align-items:center;gap:5px" },
      ],
    },
    {
      title: "Awaiting review", color: t.warn, count: 3, cards: [
        { no: 7, version: 3, title: "The warded door", words: "612w", tag: "2 flags", onClick: () => go("scene"),
          style: "background:var(--bg2);border:1px solid var(--accentLine);border-radius:10px;padding:13px 14px;cursor:pointer;box-shadow:var(--shadow)",
          tagStyle: "color:var(--bad)" },
        { no: 8, version: 1, title: "What the seal kept", words: "487w", tag: "clean", onClick: () => go("scene"),
          style: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer",
          tagStyle: "color:var(--good)" },
        { no: 6, version: 2, title: "Vael's ledger", words: "610w", tag: "1 note", onClick: () => go("scene"),
          style: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer",
          tagStyle: "color:var(--warn)" },
      ],
    },
    {
      title: "Revising", color: t.bad, count: 1, cards: [
        { no: 4, version: 3, title: "The first ascension", words: "502w", tag: "redraft", onClick: () => go("scene"),
          style: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer",
          tagStyle: "color:var(--dim)" },
      ],
    },
    {
      title: "Approved", color: t.good, count: 2, cards: [
        { no: 5, version: 1, title: "Threadbound", words: "540w", tag: "canon", onClick: () => go("scene"),
          style: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer;opacity:.78",
          tagStyle: "color:var(--good)" },
        { no: 3, version: 2, title: "The keeper wakes", words: "574w", tag: "canon", onClick: () => go("scene"),
          style: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer;opacity:.78",
          tagStyle: "color:var(--good)" },
      ],
    },
  ];

  return (
    <div>
      <div style={css("margin-bottom:24px")}>
        <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)")}>Drafting desk</h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>The Dominion Realm · Book I — scenes the Oracle has drafted and is waiting on you to judge.</p>
      </div>

      <div style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px")}>
        {STATS.map((s) => (
          <div key={s.label} style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px")}>
            <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px")}>{s.label}</div>
            <div style={css("font-family:var(--display);font-size:27px;color:var(--ink);line-height:1")}>
              {s.value}<span style={css("font-size:14px;color:var(--dim)")}>{" "}{s.suffix}</span>
            </div>
            {s.hasBar && (
              <div style={css("height:5px;border-radius:3px;background:var(--bg3);margin-top:12px;overflow:hidden")}>
                <div style={css(`height:100%;width:${s.pct};background:var(--accent)`)} />
              </div>
            )}
            {s.note && <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:9px")}>{s.note}</div>}
          </div>
        ))}
      </div>

      <div style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start")}>
        {board.map((col) => (
          <div key={col.title}>
            <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:11px;padding:0 2px")}>
              <span style={css(`width:8px;height:8px;border-radius:50%;background:${col.color}`)} />
              <span style={css("font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink)")}>{col.title}</span>
              <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{col.count}</span>
            </div>
            <div style={css("display:flex;flex-direction:column;gap:10px")}>
              {col.cards.map((c) => (
                <div key={c.no} onClick={c.onClick} style={css(c.style)}>
                  <div style={css("display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px")}>
                    <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>Scene {c.no}</span>
                    <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>v{c.version}</span>
                  </div>
                  <div style={css("font-size:13px;color:var(--dim);line-height:1.4;margin-bottom:10px")}>{c.title}</div>
                  <div style={css("display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                    <span>{c.words}</span>
                    <span style={css(c.tagStyle)}>{c.tag}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
