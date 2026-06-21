// Chapters-screen mappers: live chapters + their scenes → the Chapter / BoardScene / TimelineScene
// view-models the board, pacing graph, and timeline render from.
import { chapterTitle, sceneTitle, wordCount } from "./adapters";
import type { ChapterOut, SceneOut } from "./client";
import type { BoardScene, Chapter, TimelineScene } from "../types";

// The backend tracks several versions per scene_no; the desk only ever shows the newest. Reduce a
// chapter's scene rows to one-per-scene_no, keeping the highest `version`.
export function latestPerScene(scenes: SceneOut[]): SceneOut[] {
  const byNo = new Map<number, SceneOut>();
  for (const s of scenes) {
    const cur = byNo.get(s.scene_no);
    if (!cur || s.version > cur.version) byNo.set(s.scene_no, s);
  }
  return Array.from(byNo.values()).sort((a, b) => a.scene_no - b.scene_no);
}

// Normalize a wire scene status into the desk's display vocabulary. The desk's colours/labels key on
// approved / awaiting / drafting / revising / planned; the backend speaks draft / pending_review /
// revising / approved / superseded.
export function deskStatus(status: string): string {
  switch (status) {
    case "approved":
      return "approved";
    case "pending_review":
      return "awaiting";
    case "revising":
      return "revising";
    case "draft":
      return "drafting";
    default:
      return status;
  }
}

// Per-chapter pacing row. `words` sums the latest-version scenes; `approved` is the fraction of those
// scenes the writer has approved. There is no word target in the schema, so the screen supplies one.
export function chapterRow(chapter: ChapterOut, latest: SceneOut[], target: number): Chapter {
  const words = latest.reduce((a, s) => a + wordCount(s.prose), 0);
  const approved = latest.length ? latest.filter((s) => s.status === "approved").length / latest.length : 0;
  return { no: chapter.chapter_no, title: chapterTitle(chapter), pov: chapter.pov, target, words, approved };
}

// Board cards for one chapter (drag-reorder is local-only — see the screen).
export function boardScenes(latest: SceneOut[]): { id: string; scene: BoardScene }[] {
  return latest.map((s) => ({
    id: s.id,
    scene: { no: s.scene_no, title: sceneTitle(s), words: wordCount(s.prose), status: deskStatus(s.status) },
  }));
}

// Flatten every chapter's latest scenes into a single ordered timeline. `flags` would come from
// continuity critiques, which the chapter/scene list endpoints don't carry — so it stays 0 here.
export function timelineScenes(
  chapters: { chapter: ChapterOut; latest: SceneOut[] }[],
): TimelineScene[] {
  const rows: TimelineScene[] = [];
  for (const { chapter, latest } of chapters) {
    for (const s of latest) {
      rows.push({ n: s.scene_no, ch: chapter.chapter_no, pov: chapter.pov, status: deskStatus(s.status), flags: 0 });
    }
  }
  return rows.sort((a, b) => a.n - b.n);
}

// Distinct POVs across chapters, in first-seen order → timeline swimlanes.
export function povLanes(chapters: ChapterOut[]): string[] {
  const lanes: string[] = [];
  for (const c of chapters) if (!lanes.includes(c.pov)) lanes.push(c.pov);
  return lanes;
}
