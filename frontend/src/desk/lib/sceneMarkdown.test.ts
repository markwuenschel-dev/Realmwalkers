import { describe, expect, it } from "vitest";
import type { AnnotationOut, ChapterOut, SceneDetail, SuggestionOut } from "../api/types";
import { buildSceneMarkdown, buildScenesMarkdown, sceneMarkdownFilename } from "./sceneMarkdown";

const baseScene = (): SceneDetail => ({
  id: "s1",
  chapter_id: "ch1",
  scene_no: 2,
  version: 1,
  status: "approved",
  prose: "The door opened slowly.",
  prose_source: "draft",
  passes_run: null,
  token_count: null,
  model: null,
  created_at: "2026-01-01T00:00:00Z",
  critiques: [],
  is_exemplar: false,
});

const baseChapter = (): ChapterOut => ({
  id: "ch1",
  book_id: "b1",
  chapter_no: 3,
  title: "The Return",
  pov: "Mara",
  outline: null,
  status: "active",
});

describe("buildSceneMarkdown", () => {
  it("includes the scene heading and prose", () => {
    const md = buildSceneMarkdown(baseScene(), baseChapter(), [], []);
    expect(md).toContain("# The door opened");
    expect(md).toContain("The door opened slowly.");
  });

  it("includes chapter/scene meta in the subtitle", () => {
    const md = buildSceneMarkdown(baseScene(), baseChapter(), [], []);
    expect(md).toContain("Chapter 3");
    expect(md).toContain("POV · Mara");
    expect(md).toContain("Scene 2");
    expect(md).toContain("v1");
    expect(md).toContain("approved");
  });

  it("omits chapter meta when chapter is null", () => {
    const md = buildSceneMarkdown(baseScene(), null, [], []);
    expect(md).not.toContain("Chapter");
    expect(md).not.toContain("POV");
    expect(md).toContain("Scene 2");
  });

  it("shows _(no prose)_ when prose is null", () => {
    const scene = { ...baseScene(), prose: null };
    const md = buildSceneMarkdown(scene, null, [], []);
    expect(md).toContain("_(no prose)_");
  });

  it("omits the feedback section when there is none", () => {
    const md = buildSceneMarkdown(baseScene(), null, [], []);
    expect(md).not.toContain("Reviewer feedback");
  });

  it("renders continuity conflicts with prose vs ledger values", () => {
    const scene = {
      ...baseScene(),
      critiques: [
        {
          id: "c1",
          reviewer: "continuity",
          severity: "hard",
          note: "mismatch",
          payload: {
            attribute: "hp",
            prose_value: "10",
            ledger_value: "50",
            context_sentence: "His hp was 10.",
          },
        },
      ],
    };
    const md = buildSceneMarkdown(scene, null, [], []);
    expect(md).toContain("Continuity conflicts");
    expect(md).toContain("`10`");
    expect(md).toContain("`50`");
    expect(md).toContain('context: "His hp was 10."');
  });

  it("renders advisory reviewer notes", () => {
    const scene = {
      ...baseScene(),
      critiques: [
        {
          id: "n1",
          reviewer: "pacing",
          severity: "warn",
          note: "Scene drags in the middle.",
          payload: null,
        },
      ],
    };
    const md = buildSceneMarkdown(scene, null, [], []);
    expect(md).toContain("Reviewer notes");
    expect(md).toContain("pacing");
    expect(md).toContain("Scene drags in the middle.");
  });

  it("renders margin annotations", () => {
    const annotations: AnnotationOut[] = [
      {
        id: "a1",
        scene_id: "s1",
        version: 1,
        quote: "slowly",
        author: "Mark",
        note: "Too slow — cut adverb.",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    const md = buildSceneMarkdown(baseScene(), null, annotations, []);
    expect(md).toContain("Margin notes");
    expect(md).toContain('"slowly"');
    expect(md).toContain("Too slow");
    expect(md).toContain("*Mark*");
  });

  it("renders suggested changes with status and why", () => {
    const suggestions: SuggestionOut[] = [
      {
        id: "sg1",
        scene_id: "s1",
        version: 1,
        quote: "slowly",
        new_text: "with a creak",
        author: null,
        why: "More specific",
        status: "pending",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    const md = buildSceneMarkdown(baseScene(), null, [], suggestions);
    expect(md).toContain("Suggested changes");
    expect(md).toContain("`slowly`");
    expect(md).toContain("with a creak");
    expect(md).toContain("More specific");
  });

  it("renders _(delete)_ when new_text is null", () => {
    const suggestions: SuggestionOut[] = [
      {
        id: "sg2",
        scene_id: "s1",
        version: 1,
        quote: "slowly",
        new_text: null,
        author: null,
        why: null,
        status: "pending",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    const md = buildSceneMarkdown(baseScene(), null, [], suggestions);
    expect(md).toContain("_(delete)_");
  });

  it("ends with a single newline", () => {
    const md = buildSceneMarkdown(baseScene(), null, [], []);
    expect(md.endsWith("\n")).toBe(true);
    expect(md.endsWith("\n\n")).toBe(false);
  });
});

describe("sceneMarkdownFilename", () => {
  it("includes chapter and scene numbers when chapter is present", () => {
    expect(sceneMarkdownFilename(baseScene(), baseChapter())).toBe("scene_ch3_s2_v1.md");
  });

  it("omits chapter prefix when chapter is null", () => {
    expect(sceneMarkdownFilename(baseScene(), null)).toBe("scene_s2_v1.md");
  });
});

describe("buildScenesMarkdown", () => {
  it("joins multiple scenes with a rule separator", () => {
    const item = { scene: baseScene(), chapter: null, annotations: [], suggestions: [] };
    const md = buildScenesMarkdown([item, item]);
    expect(md).toContain("\n\n---\n\n");
  });

  it("returns a single scene with no separator", () => {
    const item = { scene: baseScene(), chapter: null, annotations: [], suggestions: [] };
    const md = buildScenesMarkdown([item]);
    expect(md).not.toContain("---\n\n");
  });

  it("ends with a single newline", () => {
    const item = { scene: baseScene(), chapter: null, annotations: [], suggestions: [] };
    const md = buildScenesMarkdown([item, item]);
    expect(md.endsWith("\n")).toBe(true);
    expect(md.endsWith("\n\n")).toBe(false);
  });
});
