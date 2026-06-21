// API wire DTOs -> Writers' Desk view-models (../types.ts). Pure functions, no React, no fetching:
// every screen reads the API through one of these so the mapping lives in exactly one place. Titles
// are optional on the wire; the desk falls back to "Chapter N" / "Scene N".
import type { ManuscriptOut, SceneOut } from "./client";
import type { MsChapter, QueueScene } from "../types";

/** Word count of a scene's prose, ignoring rendered stat-box lines (they aren't prose words). */
export function wordCount(prose: string | null | undefined): number {
  if (!prose) return 0;
  const words = prose
    .replace(/[┌┐└┘├┤│─]/g, " ") // drop box-drawing glyphs from rendered ```stat``` windows
    .trim()
    .match(/\S+/g);
  return words ? words.length : 0;
}

/** Split scene prose into display paragraphs on blank lines, preserving order. */
export function splitParagraphs(prose: string | null | undefined): string[] {
  if (!prose) return [];
  return prose
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
}

export function sceneTitle(scene: { title?: string | null; scene_no: number }): string {
  return scene.title?.trim() || `Scene ${scene.scene_no}`;
}

export function chapterTitle(ch: { title?: string | null; chapter_no: number }): string {
  return ch.title?.trim() || `Chapter ${ch.chapter_no}`;
}

/** The assembled manuscript -> the reader's chapter/paragraph view-model. */
export function toMsChapters(m: ManuscriptOut): MsChapter[] {
  return m.chapters.map((ch) => ({
    no: ch.chapter_no,
    title: chapterTitle(ch),
    pov: ch.pov,
    paras: ch.scenes.flatMap((s) => splitParagraphs(s.prose)),
  }));
}

/** Pending/awaiting scenes -> the review-queue rows used for j/k navigation and the inbox. */
export function toQueueScenes(scenes: SceneOut[]): QueueScene[] {
  return scenes.map((s) => ({
    no: s.scene_no,
    title: sceneTitle(s),
    words: String(wordCount(s.prose)),
    version: s.version,
    status: s.status,
  }));
}
