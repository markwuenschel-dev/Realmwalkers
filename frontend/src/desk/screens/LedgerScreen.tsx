import { css } from "../css";
import { useDesk } from "../state";
import { LEDGER_CATS, LEDGER_CHARS, LEDGER_THREADS } from "../data";

export default function LedgerScreen() {
  const { t, ledgerCat, selectedThread, setLedgerCat, selectThread } = useDesk();

  const isCharCat = ledgerCat === "characters";
  const isThreadsCat = ledgerCat === "threads";
  const isOtherCat = !isCharCat && !isThreadsCat;

  const threadKinds: Record<string, string> = {
    relationship: t.bad, mentorship: t.info, system: t.accent, power: t.warn,
  };
  const threadCards = LEDGER_THREADS.map((th) => {
    const sel = selectedThread === th.id;
    const kindColor = threadKinds[th.kind] || t.dim;
    return {
      id: th.id, name: th.name, kind: th.kind, state: th.state, note: th.note,
      cardStyle: `background:var(--bg2);border:1px solid ${sel ? "var(--accentLine)" : "var(--line)"};border-radius:var(--r);padding:16px 18px;cursor:pointer;box-shadow:${sel ? "var(--shadow)" : "none"}`,
      kindStyle: `font-family:var(--mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:${kindColor};background:color-mix(in srgb,${kindColor} 13%,transparent);border-radius:999px;padding:3px 9px`,
      beats: th.beats.map((b, i) => ({
        s: b.s, label: b.label, notLast: i !== th.beats.length - 1, flag: !!b.flag,
        chipStyle: `display:flex;flex-direction:column;gap:2px;padding:7px 11px;border-radius:8px;border:1px solid ${b.flag ? "color-mix(in srgb,var(--bad) 40%,var(--line))" : "var(--line)"};background:${b.flag ? "color-mix(in srgb,var(--bad) 9%,var(--bg3))" : "var(--bg3)"};white-space:nowrap`,
      })),
    };
  });

  return (
    <div>
      <div style={css("margin-bottom:22px")}>
        <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)")}>World ledger</h1>
        <p style={css("margin:0;color:var(--dim);font-size:14px")}>The Oracle's canon — every fact the continuity passes check prose against.</p>
      </div>
      <div style={css("display:grid;grid-template-columns:184px 1fr;gap:22px;align-items:start")}>
        <div style={css("display:flex;flex-direction:column;gap:3px;position:sticky;top:84px")}>
          {LEDGER_CATS.map((cat) => {
            const active = ledgerCat === cat.id;
            return (
              <button
                key={cat.id}
                onClick={() => setLedgerCat(cat.id)}
                style={css(`display:flex;align-items:center;width:100%;padding:9px 12px;border:1px solid ${active ? "var(--accentLine)" : "transparent"};border-radius:8px;background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:13.5px;cursor:pointer`)}
              >
                {cat.label}
                <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{cat.count}</span>
              </button>
            );
          })}
        </div>

        <div style={css("min-width:0")}>
          {isCharCat && (
            <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:14px")}>
              {LEDGER_CHARS.map((ch) => (
                <div key={ch.name} style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);overflow:hidden")}>
                  <div style={css("display:flex;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid var(--line);background:var(--bg2b)")}>
                    <div style={css("width:38px;height:38px;border-radius:9px;background:var(--accentSoft);border:1px solid var(--accentLine);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:17px;color:var(--accent);flex:none")}>{ch.initial}</div>
                    <div style={css("min-width:0")}>
                      <div style={css("font-family:var(--display);font-size:16px;color:var(--ink)")}>{ch.name}</div>
                      <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--dim);margin-top:2px")}>{ch.role}</div>
                    </div>
                  </div>
                  <div style={css("padding:13px 16px")}>
                    {ch.attrs.map((at, i) => (
                      <div key={i} style={css("display:flex;justify-content:space-between;gap:12px;padding:5px 0;font-size:13px;border-bottom:1px solid var(--hairline)")}>
                        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>{at.k}</span>
                        <span style={css("color:var(--ink);text-align:right")}>{at.v}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {isThreadsCat && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <p style={css("margin:0 0 4px;font-size:13px;color:var(--dim);line-height:1.5")}>Follow a relationship or plot thread across every scene it touches. Flagged beats carry an open continuity conflict.</p>
              {threadCards.map((th) => (
                <div key={th.id} onClick={() => selectThread(th.id)} style={css(th.cardStyle)}>
                  <div style={css("display:flex;align-items:center;gap:11px;margin-bottom:8px;flex-wrap:wrap")}>
                    <span style={css("font-family:var(--display);font-size:18px;color:var(--ink);white-space:nowrap")}>{th.name}</span>
                    <span style={css(th.kindStyle)}>{th.kind}</span>
                    <span style={css("margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>state · {th.state}</span>
                  </div>
                  <p style={css("margin:0 0 14px;font-size:13.5px;color:var(--dim);line-height:1.55")}>{th.note}</p>
                  <div style={css("display:flex;align-items:center;flex-wrap:wrap;row-gap:10px")}>
                    {th.beats.map((b, i) => (
                      <div key={i} style={css("display:flex;align-items:center")}>
                        <div style={css(b.chipStyle)}>
                          <span style={css("font-family:var(--mono);font-size:9px;letter-spacing:.03em;color:var(--dim)")}>SCENE {b.s}</span>
                          <span style={css("font-size:12.5px;color:var(--ink)")}>{b.label}</span>
                        </div>
                        {b.notLast && <span style={css("margin:0 9px;color:var(--dim);font-size:13px")}>→</span>}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {isOtherCat && (
            <div style={css("background:var(--bg2);border:1px dashed var(--line);border-radius:var(--r);padding:40px;text-align:center;font-family:var(--mono);font-size:12.5px;color:var(--dim)")}>This ledger section isn't populated in the prototype yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
