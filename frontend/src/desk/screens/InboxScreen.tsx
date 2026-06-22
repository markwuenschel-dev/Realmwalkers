import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { sceneLabel, wordCount } from "../lib/format";
import Planner from "../components/Planner";
import type { SceneOut } from "../api/types";

// Latest version of each (chapter, scene) — the scene's *current* state for the board.
function latestByScene(scenes: SceneOut[]): SceneOut[] {
  const m = new Map<string, SceneOut>();
  for (const s of scenes) {
    const key = `${s.chapter_id}:${s.scene_no}`;
    const prev = m.get(key);
    if (!prev || s.version > prev.version) m.set(key, s);
  }
  return [...m.values()];
}

export default function InboxScreen() {
  const { t, go, openScene } = useDesk();
  const data = useDeskData();

  const latest = latestByScene(data.scenes);
  const approved = latest.filter((s) => s.status === "approved");
  const revising = latest.filter((s) => s.status === "revision_requested");

  const manuscriptWords = (data.manuscript?.chapters ?? [])
    .flatMap((c) => c.scenes)
    .reduce((acc, s) => acc + wordCount(s.prose), 0);

  const stats = [
    { label: "Manuscript", value: manuscriptWords.toLocaleString(), suffix: "words" },
    { label: "Scenes approved", value: String(approved.length), suffix: `/ ${latest.length || 0} drafted` },
    { label: "Awaiting you", value: String(data.pending.length), suffix: "scenes",
      note: data.pending.length ? "ready for review" : "queue clear" },
    { label: "Drafting", value: data.jobs.running ? "1" : "0",
      suffix: data.jobs.running ? "in progress" : "idle",
      note: data.jobs.queued ? `${data.jobs.queued} queued` : data.jobs.failed ? `${data.jobs.failed} failed` : undefined },
  ];

  const cardBase = "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px";
  const sceneCard = (s: SceneOut, color: string, tag: string, onClick?: () => void) => (
    <div
      key={s.id}
      onClick={onClick}
      style={css(`${cardBase};border-left:3px solid ${color};${onClick ? "cursor:pointer;box-shadow:var(--shadow)" : "opacity:.8"}`)}
    >
      <div style={css("display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px")}>
        <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>Scene {s.scene_no}</span>
        <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>v{s.version}</span>
      </div>
      <div style={css("font-size:13px;color:var(--dim);line-height:1.4;margin-bottom:10px")}>{sceneLabel(s)}</div>
      <div style={css("display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
        <span>{wordCount(s.prose)} words</span>
        <span style={css(`color:${color}`)}>{tag}</span>
      </div>
    </div>
  );

  return (
    <div>
      <div style={css("margin-bottom:24px")}>
        <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)")}>Drafting desk</h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>
          {data.books.find((b) => b.id === data.bookId)?.title ?? "No book yet"} — plan a chapter, then judge what the Oracle drafts. Nothing runs until you ask it to.
        </p>
      </div>

      <Planner />

      <div style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px")}>
        {stats.map((s) => (
          <div key={s.label} style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px")}>
            <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px")}>{s.label}</div>
            <div style={css("font-family:var(--display);font-size:27px;color:var(--ink);line-height:1")}>
              {s.value}<span style={css("font-size:14px;color:var(--dim)")}>{" "}{s.suffix}</span>
            </div>
            {s.note && <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:9px")}>{s.note}</div>}
          </div>
        ))}
      </div>

      <div style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start")}>
        {/* Drafting (live worker state) */}
        <div>
          <Column title="Drafting" color={t.info} count={data.jobs.running ? 1 : 0} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {data.jobs.running && data.jobs.active_scene ? (
              <div style={css(`${cardBase};border:1px dashed var(--line)`)}>
                <div style={css("font-family:var(--display);font-size:15px;color:var(--ink);margin-bottom:6px")}>
                  Scene {data.jobs.active_scene.scene_no ?? "?"}
                </div>
                <div style={css("color:var(--info);font-family:var(--mono);font-size:11px")}>writing…</div>
              </div>
            ) : (
              <Empty text={data.jobs.queued ? `${data.jobs.queued} queued` : "idle"} />
            )}
          </div>
        </div>

        {/* Awaiting review (pending queue) */}
        <div>
          <Column title="Awaiting review" color={t.warn} count={data.pending.length} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {data.pending.length === 0 && <Empty text="nothing to review" />}
            {data.pending.map((s, i) => sceneCard(s, t.warn, "review →", () => openScene(i)))}
          </div>
        </div>

        {/* Revising */}
        <div>
          <Column title="Revising" color={t.bad} count={revising.length} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {revising.length === 0 && <Empty text="—" />}
            {revising.map((s) => sceneCard(s, t.bad, "redrafting"))}
          </div>
        </div>

        {/* Approved */}
        <div>
          <Column title="Approved" color={t.good} count={approved.length} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {approved.length === 0 && <Empty text="—" />}
            {approved
              .sort((a, b) => a.scene_no - b.scene_no)
              .map((s) => sceneCard(s, t.good, "canon", () => go("manuscript")))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Column({ title, color, count }: { title: string; color: string; count: number }) {
  return (
    <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:11px;padding:0 2px")}>
      <span style={css(`width:8px;height:8px;border-radius:50%;background:${color}`)} />
      <span style={css("font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink)")}>{title}</span>
      <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{count}</span>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div style={css("border:1px dashed var(--line);border-radius:10px;padding:16px;text-align:center;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{text}</div>
  );
}
