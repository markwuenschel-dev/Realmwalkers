import { css } from "../css";
import { api } from "../api/client";
import { toMsChapters } from "../api/adapters";
import { useFetch, useSelectedBook } from "../api/hooks";

export default function ManuscriptScreen() {
  const { bookId } = useSelectedBook();
  const { data, loading, error } = useFetch(
    () => (bookId ? api.manuscript(bookId) : Promise.resolve(null)),
    [bookId],
  );

  const chapters = data ? toMsChapters(data) : [];
  const bookTitle = data?.title ?? "The Dominion Realm";

  const note = (msg: string) => (
    <div style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:12px;letter-spacing:.04em;padding:40px 0")}>
      {msg}
    </div>
  );

  return (
    <div>
      <article style={css("max-width:40rem;margin:0 auto;padding:20px 0 60px")}>
        <div style={css("text-align:center;margin-bottom:64px;padding-bottom:40px;border-bottom:1px solid var(--line)")}>
          <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);margin-bottom:20px")}>Book One</div>
          <h1 style={css("margin:0 0 14px;font-family:var(--display);font-weight:600;font-size:46px;letter-spacing:.01em;color:var(--ink)")}>{bookTitle}</h1>
          <div style={css("font-family:var(--prose);font-style:italic;font-size:16px;color:var(--dim)")}>a thread-ledger chronicle</div>
        </div>
        {loading && note("Loading the manuscript…")}
        {error && note(`Could not load the manuscript — ${error}`)}
        {!loading && !error && chapters.length === 0 && note("No approved scenes yet — nothing in the manuscript.")}
        {chapters.map((ch) => (
          <section key={ch.no} style={css("margin-bottom:54px")}>
            <div style={css("text-align:center;margin-bottom:30px")}>
              <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:9px")}>Chapter {ch.no}</div>
              <h2 style={css("margin:0;font-family:var(--display);font-weight:500;font-size:25px;color:var(--ink)")}>
                {ch.title}
                <span style={css("display:block;font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-top:8px;font-weight:400")}>POV · {ch.pov}</span>
              </h2>
            </div>
            {ch.paras.map((p, i) => (
              <p key={i} style={css("font-family:var(--prose);font-size:18.5px;line-height:1.9;color:var(--ink);margin:0 0 1.15em;text-align:justify;hyphens:auto")}>{p}</p>
            ))}
            <div style={css("text-align:center;color:var(--accent);font-size:15px;letter-spacing:.5em;margin:30px 0 4px")}>✦</div>
          </section>
        ))}
      </article>
    </div>
  );
}
