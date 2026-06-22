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

// Fold accepted tracked-changes into prose: replace the first literal occurrence of each `quote`
// with its `new_text` (empty = deletion). Mirrors the substring anchoring the Desk uses for markers.
export const applyAcceptedSuggestions = (
  prose: string,
  suggestions: { quote: string; new_text: string | null; status: string }[],
): string => {
  let out = prose;
  for (const s of suggestions) {
    if (s.status !== "accepted" || !s.quote || !out.includes(s.quote)) continue;
    const repl = s.new_text ?? "";
    out = out.replace(s.quote, () => repl); // function form avoids $-pattern surprises in new_text
  }
  return out;
};
