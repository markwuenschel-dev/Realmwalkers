import { describe, expect, it } from "vitest";
import {
  chapterLabel,
  isKnownChapterKind,
  partKindWord,
  partLabel,
  resolveChapterLabel,
  sectionLabel,
  sectionTypeLabel,
  toRoman,
  volumeLabel,
} from "./labels";

describe("toRoman", () => {
  it("converts common part numbers", () => {
    expect(toRoman(1)).toBe("I");
    expect(toRoman(2)).toBe("II");
    expect(toRoman(4)).toBe("IV");
    expect(toRoman(9)).toBe("IX");
    expect(toRoman(14)).toBe("XIV");
    expect(toRoman(40)).toBe("XL");
  });
  it("falls back to the arabic number for out-of-range input", () => {
    expect(toRoman(0)).toBe("0");
    expect(toRoman(-3)).toBe("-3");
    expect(toRoman(1.5)).toBe("1.5");
  });
});

describe("partLabel", () => {
  it("renders 'Part <roman> — <title>' and drops the dash when untitled", () => {
    expect(partLabel({ part_no: 1, title: "The Gathering Storm" })).toBe(
      "Part I — The Gathering Storm",
    );
    expect(partLabel({ part_no: 2, title: "" })).toBe("Part II");
    expect(partLabel({ part_no: 3 })).toBe("Part III");
  });
  it("uses the Act word for kind=act (structurally still a Part)", () => {
    expect(partLabel({ part_no: 2, title: "Rising", kind: "act" })).toBe("Act II — Rising");
    expect(partLabel({ part_no: 3, kind: "act" })).toBe("Act III");
    expect(partKindWord("act")).toBe("Act");
    expect(partKindWord("part")).toBe("Part");
    expect(partKindWord(null)).toBe("Part");
  });
});

describe("volumeLabel", () => {
  it("renders 'Volume <roman> — <title>' and drops the dash when untitled", () => {
    expect(volumeLabel({ volume_no: 1, title: "The Long Winter" })).toBe(
      "Volume I — The Long Winter",
    );
    expect(volumeLabel({ volume_no: 2, title: "" })).toBe("Volume II");
    expect(volumeLabel({ volume_no: 3 })).toBe("Volume III");
  });
});

describe("chapterLabel", () => {
  it("numbers a plain chapter and names the other known kinds", () => {
    expect(chapterLabel({ kind: "chapter", chapter_no: 3 })).toBe("Chapter 3");
    expect(chapterLabel({ kind: "prologue", chapter_no: 1 })).toBe("Prologue");
    expect(chapterLabel({ kind: "interlude", chapter_no: 5 })).toBe("Interlude");
    expect(chapterLabel({ kind: "epilogue", chapter_no: 40 })).toBe("Epilogue");
  });
  it("falls back to 'Chapter N' for an unknown/absent kind (never a raw enum string)", () => {
    expect(chapterLabel({ kind: "sidebar", chapter_no: 7 })).toBe("Chapter 7");
    expect(chapterLabel({ kind: null, chapter_no: 8 })).toBe("Chapter 8");
    expect(chapterLabel({ chapter_no: 9 })).toBe("Chapter 9");
  });
});

describe("sectionLabel", () => {
  it("prefers the author title for front/back matter, falling back to the kind label", () => {
    expect(sectionLabel({ kind: "front_matter", title: "Dramatis Personae", chapter_no: 1 })).toBe(
      "Dramatis Personae",
    );
    expect(sectionLabel({ kind: "back_matter", title: "Glossary", chapter_no: 30 })).toBe(
      "Glossary",
    );
    expect(sectionLabel({ kind: "front_matter", title: null, chapter_no: 1 })).toBe("Front Matter");
    expect(sectionLabel({ kind: "back_matter", title: "", chapter_no: 30 })).toBe("Back Matter");
  });
  it("delegates to chapterLabel for non-section kinds", () => {
    expect(sectionLabel({ kind: "chapter", title: "Ignored", chapter_no: 2 })).toBe("Chapter 2");
    expect(sectionLabel({ kind: "prologue", title: "Ignored", chapter_no: 1 })).toBe("Prologue");
  });
  it("prefers explicit title, then section_type display name, then kind label", () => {
    // title wins
    expect(
      sectionLabel({
        kind: "back_matter",
        title: "Glossary of Terms",
        section_type: "glossary",
        chapter_no: 30,
      }),
    ).toBe("Glossary of Terms");
    // no title → section_type display name
    expect(
      sectionLabel({ kind: "back_matter", title: null, section_type: "glossary", chapter_no: 30 }),
    ).toBe("Glossary");
    expect(
      sectionLabel({
        kind: "front_matter",
        title: null,
        section_type: "dramatis_personae",
        chapter_no: 1,
      }),
    ).toBe("Dramatis Personae");
    // neither → kind label
    expect(
      sectionLabel({ kind: "front_matter", title: null, section_type: null, chapter_no: 1 }),
    ).toBe("Front Matter");
  });
});

describe("sectionTypeLabel", () => {
  it("maps known slugs, title-cases unknown ones, and returns undefined for blank", () => {
    expect(sectionTypeLabel("glossary")).toBe("Glossary");
    expect(sectionTypeLabel("dramatis_personae")).toBe("Dramatis Personae");
    expect(sectionTypeLabel("family_tree")).toBe("Family Tree"); // unknown slug → title-cased
    expect(sectionTypeLabel("")).toBeUndefined();
    expect(sectionTypeLabel(null)).toBeUndefined();
  });
});

describe("resolveChapterLabel dispatch", () => {
  it("uses sectionLabel for section kinds and chapterLabel otherwise", () => {
    expect(resolveChapterLabel({ kind: "front_matter", title: "Map", chapter_no: 1 })).toBe("Map");
    expect(resolveChapterLabel({ kind: "chapter", title: "The Scrim", chapter_no: 4 })).toBe(
      "Chapter 4",
    );
    expect(resolveChapterLabel({ kind: "prologue", title: null, chapter_no: 1 })).toBe("Prologue");
  });
});

describe("isKnownChapterKind", () => {
  it("recognizes the enum members and rejects everything else", () => {
    for (const k of [
      "chapter",
      "prologue",
      "interlude",
      "epilogue",
      "front_matter",
      "back_matter",
    ]) {
      expect(isKnownChapterKind(k)).toBe(true);
    }
    expect(isKnownChapterKind("sidebar")).toBe(false);
    expect(isKnownChapterKind(null)).toBe(false);
    expect(isKnownChapterKind(undefined)).toBe(false);
  });
});
