import { describe, expect, it } from "vitest";
import {
  applyAcceptedSuggestions,
  sceneLabel,
  snippet,
  statValue,
  wordCount,
} from "./format";

describe("wordCount", () => {
  it("returns 0 for null/undefined/empty", () => {
    expect(wordCount(null)).toBe(0);
    expect(wordCount(undefined)).toBe(0);
    expect(wordCount("")).toBe(0);
    expect(wordCount("   ")).toBe(0);
  });

  it("counts space-separated tokens", () => {
    expect(wordCount("one two three")).toBe(3);
  });

  it("collapses internal whitespace", () => {
    expect(wordCount("one\n  two\tthree")).toBe(3);
  });
});

describe("snippet", () => {
  it("returns empty string for falsy input", () => {
    expect(snippet(null)).toBe("");
    expect(snippet(undefined)).toBe("");
    expect(snippet("")).toBe("");
  });

  it("returns the full text when at or under the word limit", () => {
    expect(snippet("one two three", 3)).toBe("one two three");
    expect(snippet("one two", 3)).toBe("one two");
  });

  it("truncates to the word limit and appends ellipsis", () => {
    expect(snippet("one two three four", 3)).toBe("one two three…");
  });

  it("respects the default limit of 7", () => {
    const words = "a b c d e f g h".split(" ");
    expect(snippet(words.join(" "))).toBe("a b c d e f g…");
  });

  it("collapses extra whitespace before slicing", () => {
    expect(snippet("  one   two  three  ", 2)).toBe("one two…");
  });
});

describe("sceneLabel", () => {
  it("returns the prose snippet when prose is present", () => {
    const scene = { scene_no: 1, prose: "The sun rose slowly over the mountain ridge." };
    expect(sceneLabel(scene)).toBe("The sun rose slowly over the…");
  });

  it("falls back to 'Scene N' when prose is null", () => {
    expect(sceneLabel({ scene_no: 3, prose: null })).toBe("Scene 3");
  });

  it("falls back to 'Scene N' when prose is empty", () => {
    expect(sceneLabel({ scene_no: 7, prose: "   " })).toBe("Scene 7");
  });
});

describe("statValue", () => {
  it("returns em-dash for null/undefined", () => {
    expect(statValue(null)).toBe("—");
    expect(statValue(undefined)).toBe("—");
  });

  it("joins arrays with comma-space", () => {
    expect(statValue(["a", "b", "c"])).toBe("a, b, c");
  });

  it("JSON-stringifies plain objects", () => {
    expect(statValue({ hp: 10 })).toBe('{"hp":10}');
  });

  it("converts primitives to string", () => {
    expect(statValue(42)).toBe("42");
    expect(statValue(true)).toBe("true");
    expect(statValue("hello")).toBe("hello");
  });
});

describe("applyAcceptedSuggestions", () => {
  it("replaces an accepted suggestion's quote with new_text", () => {
    const result = applyAcceptedSuggestions("The quick brown fox.", [
      { quote: "quick brown", new_text: "slow grey", status: "accepted" },
    ]);
    expect(result).toBe("The slow grey fox.");
  });

  it("deletes the quote when new_text is null", () => {
    const result = applyAcceptedSuggestions("Hello cruel world.", [
      { quote: "cruel ", new_text: null, status: "accepted" },
    ]);
    expect(result).toBe("Hello world.");
  });

  it("ignores pending and rejected suggestions", () => {
    const prose = "The quick brown fox.";
    const result = applyAcceptedSuggestions(prose, [
      { quote: "quick", new_text: "slow", status: "pending" },
      { quote: "brown", new_text: "grey", status: "rejected" },
    ]);
    expect(result).toBe(prose);
  });

  it("skips a suggestion whose quote is not found", () => {
    const prose = "The quick brown fox.";
    const result = applyAcceptedSuggestions(prose, [
      { quote: "missing phrase", new_text: "replacement", status: "accepted" },
    ]);
    expect(result).toBe(prose);
  });

  it("applies multiple accepted suggestions in order", () => {
    const result = applyAcceptedSuggestions("alpha beta gamma", [
      { quote: "alpha", new_text: "one", status: "accepted" },
      { quote: "gamma", new_text: "three", status: "accepted" },
    ]);
    expect(result).toBe("one beta three");
  });

  it("handles new_text containing $ without mangling it", () => {
    const result = applyAcceptedSuggestions("cost is X dollars", [
      { quote: "X", new_text: "$100", status: "accepted" },
    ]);
    expect(result).toBe("cost is $100 dollars");
  });
});
