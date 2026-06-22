// Small presentational helpers. Real prose has no titles (those were fixtures), so a scene's display
// label is derived from its opening words.

export const wordCount = (prose: string | null | undefined): number =>
  prose ? prose.trim().match(/\S+/g)?.length ?? 0 : 0;

export const snippet = (prose: string | null | undefined, words = 7): string => {
  if (!prose) return "";
  const clean = prose.replace(/\s+/g, " ").trim();
  const parts = clean.split(" ");
  return parts.slice(0, words).join(" ") + (parts.length > words ? "…" : "");
};

export const sceneLabel = (scene: { scene_no: number; prose: string | null }): string =>
  snippet(scene.prose, 6) || `Scene ${scene.scene_no}`;

// "412 / 480", ["a","b"] -> "a, b", objects -> JSON. Stats come back as arbitrary JSON.
export const statValue = (v: unknown): string => {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.map(String).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};
