import { css } from "../css";
import { useDeskData } from "../api/data";
import { seg } from "../prose";

export default function ManuscriptScreen() {
  const { manuscript } = useDeskData();
  const chapters = manuscript?.chapters ?? [];
  const hasProse = chapters.some((c) => c.scenes.some((s) => (s.prose ?? "").trim()));

  return (
    <div>
      <article style={css("max-width:40rem;margin:0 auto;padding:20px 0 60px")}>
        <div style={css("text-align:center;margin-bottom:64px;padding-bottom:40px;border-bottom:1px solid var(--line)")}>
          <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);margin-bottom:20px")}>Book One</div>
          <h1 style={css("margin:0 0 14px;font-family:var(--display);font-weight:600;font-size:46px;letter-spacing:.01em;color:var(--ink)")}>{manuscript?.title ?? "—"}</h1>
          <div style={css("font-family:var(--prose);font-style:italic;font-size:16px;color:var(--dim)")}>the approved manuscript, in reading order</div>
        </div>

        {!hasProse && (
          <p style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:13px")}>No approved scenes yet — approve a scene in the inbox and it lands here.</p>
        )}

        {chapters.map((ch) => (
          <section key={ch.chapter_no} style={css("margin-bottom:54px")}>
            <div style={css("text-align:center;margin-bottom:30px")}>
              <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:9px")}>Chapter {ch.chapter_no}</div>
              <h2 style={css("margin:0;font-family:var(--display);font-weight:500;font-size:25px;color:var(--ink)")}>
                <span style={css("display:block;font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);font-weight:400")}>POV · {ch.pov}</span>
              </h2>
            </div>
            {ch.scenes.flatMap((sc) =>
              seg(sc.prose ?? "").map((b, i) => (
                <p key={`${sc.scene_no}-${i}`} style={css("font-family:var(--prose);font-size:18.5px;line-height:1.9;color:var(--ink);margin:0 0 1.15em;text-align:justify;hyphens:auto")}>{b.text}</p>
              )),
            )}
            <div style={css("text-align:center;color:var(--accent);font-size:15px;letter-spacing:.5em;margin:30px 0 4px")}>✦</div>
          </section>
        ))}
      </article>
    </div>
  );
}
