import { describe, expect, it } from "vitest";
import type { CreatureKind, Intensity, MagicDomain, UiRole } from "../prose";
import {
  AA_SMALL_TEXT,
  contrastRatio,
  formatInterfaceHeader,
  neutralSurface,
  readableText,
  resolveSurface,
  tableSurface,
  type Surface,
} from "./litrpgSurfaces";

// Compile-time totality guards. `satisfies` fails the typecheck when a union gains a member that is
// missing here (or keeps a stale one), so a new role / domain / creature cannot ship without its
// surface being exercised below — the exact gap that let levelup/skill/sheet reach the render path
// with no ROLE_STYLES entry.
const ROLE_KEYS = {
  system: true,
  warning: true,
  combat: true,
  damage: true,
  healing: true,
  defense: true,
  resource: true,
  progression: true,
  xp: true,
  crafting: true,
  insight: true,
  corruption: true,
  name: true,
  vow: true,
  item: true,
  levelup: true,
  skill: true,
  sheet: true,
} satisfies Record<UiRole, true>;

const DOMAIN_KEYS = {
  fire: true,
  water: true,
  air: true,
  earth: true,
  light: true,
  shadow: true,
  life: true,
  death: true,
  runic: true,
  blood: true,
  spirit: true,
  mind: true,
  force: true,
  chaos: true,
  celestial: true,
  void: true,
  planar: true,
  time: true,
  entropy: true,
  eldritch: true,
  aether: true,
} satisfies Record<MagicDomain, true>;

const CREATURE_KEYS = {
  mortal: true,
  beast: true,
  monster: true,
  demon: true,
  archdemon: true,
  angel: true,
  archangel: true,
  undead: true,
  dragon: true,
  construct: true,
  spirit: true,
  fae: true,
  celestial: true,
  voidborn: true,
  eldritch: true,
  xyloryn: true,
  nhal: true,
} satisfies Record<CreatureKind, true>;

const ROLES = Object.keys(ROLE_KEYS) as UiRole[];
const DOMAINS = Object.keys(DOMAIN_KEYS) as MagicDomain[];
const CREATURES = Object.keys(CREATURE_KEYS) as CreatureKind[];
const INTENSITIES: Intensity[] = ["subtle", "standard", "strong", "apex"];

const HEX = /^[0-9A-F]{6}$/;

/** Mirrors the module's own perceived-luminance formula (0 = black, 1 = white). */
function lum(hex: string): number {
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

// An INDEPENDENT WCAG contrast implementation — deliberately not the module's own contrastRatio(), so
// these tests measure the palette rather than rubber-stamp the helper that produced it.
function contrast(a: string, b: string): number {
  const rel = (hex: string) => {
    const [r, g, bl] = [0, 2, 4]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [rel(a), rel(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Every invariant a Surface must hold for Word to render it legibly, whatever spec produced it. */
function expectUsableSurface(s: Surface, where: string): void {
  for (const field of [
    "accent",
    "fill",
    "headerFill",
    "border",
    "text",
    "headerText",
    "labelColor",
  ] as const) {
    expect(String(s[field]), `${where} · ${field}`).toMatch(HEX);
  }
  expect(s.text, `${where} · text`).toBe(readableText(s.fill));
  expect(s.headerText, `${where} · headerText`).toBe(readableText(s.headerFill));
  // The load-bearing one: label text is small caps on the body fill, so it must clear WCAG AA. This is
  // a property of the whole palette — a new domain with a washed-out accent/darkFill pair fails here.
  expect(
    contrast(s.labelColor, s.fill),
    `${where} · labelColor ${s.labelColor} on fill ${s.fill}`,
  ).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
  expect(s.leftBorderSize, `${where} · leftBorderSize`).toBeGreaterThan(0);
}

describe("readableText", () => {
  it("returns white on dark fills", () => {
    expect(readableText("0B0F14")).toBe("FFFFFF");
    expect(readableText("1F2937")).toBe("FFFFFF");
  });

  it("returns dark on light fills", () => {
    expect(readableText("FFFFFF")).toBe("111827");
    expect(readableText("F8FAFC")).toBe("111827");
  });
});

describe("formatInterfaceHeader", () => {
  it("formats creature scan with domain", () => {
    expect(
      formatInterfaceHeader({
        role: "insight",
        creature: "archdemon",
        domain: "death",
      }),
    ).toBe("[ INSIGHT ] CREATURE SCAN · ARCHDEMON · DEATH");
  });

  it("formats nhal as warning creature scan", () => {
    expect(formatInterfaceHeader({ role: "warning", creature: "nhal" })).toBe(
      "[ WARNING ] CREATURE SCAN · N'HAL",
    );
  });

  it("formats progression with domain", () => {
    expect(formatInterfaceHeader({ role: "progression", domain: "fire" })).toBe(
      "[ PROGRESSION ] PROGRESSION · FIRE",
    );
  });

  it("formats domain-only healing", () => {
    expect(formatInterfaceHeader({ role: "healing", domain: "life" })).toBe("[ HEALING ] LIFE");
  });
});

describe("resolveSurface", () => {
  it("returns distinct accents for different specs", () => {
    const archdemon = resolveSurface({
      role: "insight",
      creature: "archdemon",
      domain: "death",
      intensity: "strong",
    });
    const archangel = resolveSurface({
      role: "healing",
      creature: "archangel",
      domain: "light",
      intensity: "standard",
    });
    const progression = resolveSurface({
      role: "progression",
      domain: "fire",
      intensity: "standard",
    });
    const damage = resolveSurface({
      role: "damage",
      domain: "death",
      intensity: "strong",
    });

    expect(archdemon.accent).not.toBe(archangel.accent);
    expect(progression.accent).not.toBe(damage.accent);
    expect(archdemon.leftBorderSize).toBe(24);
    expect(archangel.leftBorderSize).toBe(16);
  });

  it("apex uses dark header and pale body", () => {
    const apex = resolveSurface({
      role: "insight",
      creature: "archdemon",
      domain: "death",
      intensity: "apex",
    });
    expect(apex.headerFill).not.toBe(apex.fill);
    expect(apex.headerText).toBe("FFFFFF");
    expect(apex.leftBorderSize).toBe(32);
    expect(readableText(apex.fill)).toBe("111827");
  });

  it("returns distinct surfaces for crafting and xp", () => {
    const crafting = resolveSurface({ role: "crafting" });
    const xp = resolveSurface({ role: "xp" });
    expect(crafting.accent).not.toBe(xp.accent);
  });
});

describe("resolveSurface — totality", () => {
  it("resolves a usable surface for every UiRole", () => {
    expect(ROLES).toHaveLength(18);
    for (const role of ROLES) expectUsableSurface(resolveSurface({ role }), `role=${role}`);
  });

  it("resolves a usable surface for every MagicDomain", () => {
    expect(DOMAINS).toHaveLength(21);
    for (const domain of DOMAINS) {
      expectUsableSurface(resolveSurface({ domain }), `domain=${domain}`);
    }
  });

  it("resolves a usable surface for every CreatureKind", () => {
    expect(CREATURES).toHaveLength(17);
    for (const creature of CREATURES) {
      expectUsableSurface(resolveSurface({ creature }), `creature=${creature}`);
    }
  });

  it("resolves a usable surface for every role × domain × intensity", () => {
    for (const role of ROLES) {
      for (const domain of DOMAINS) {
        for (const intensity of INTENSITIES) {
          expectUsableSurface(
            resolveSurface({ role, domain, intensity }),
            `${role}/${domain}/${intensity}`,
          );
        }
      }
    }
  });

  it("resolves a usable surface for every creature × domain × intensity", () => {
    for (const creature of CREATURES) {
      for (const domain of DOMAINS) {
        for (const intensity of INTENSITIES) {
          expectUsableSurface(
            resolveSurface({ creature, domain, intensity }),
            `${creature}/${domain}/${intensity}`,
          );
        }
      }
    }
  });

  it("gives all 21 domains distinct accents", () => {
    const accents = DOMAINS.map((d) => resolveSurface({ domain: d }).accent);
    expect(new Set(accents).size).toBe(DOMAINS.length);
  });

  it("falls back to the system surface for an empty spec", () => {
    expect(resolveSurface()).toEqual(resolveSurface({ role: "system" }));
  });
});

describe("resolveSurface — the three new roles", () => {
  it("gives levelup / sheet / skill their own resolvable surfaces", () => {
    expect(resolveSurface({ role: "levelup" }).fill).toBe("FFFDF4");
    expect(resolveSurface({ role: "sheet" }).headerFill).toBe("E5B52A");
    // skill is neutral until a domain drives it (the panel is domain-coded, not role-coded).
    expect(resolveSurface({ role: "skill" }).accent).toBe("475569");
  });

  it("lets domain= drive the skill surface", () => {
    const fire = resolveSurface({ role: "skill", domain: "fire" });
    expect(fire.accent).toBe("D23A17");
    expect(fire.accent).not.toBe(resolveSurface({ role: "skill" }).accent);
  });
});

describe("contrastRatio", () => {
  it("spans the WCAG range and is symmetric", () => {
    expect(contrastRatio("000000", "FFFFFF")).toBeCloseTo(21, 5);
    expect(contrastRatio("FFFFFF", "000000")).toBeCloseTo(21, 5);
    expect(contrastRatio("7C3AED", "7C3AED")).toBeCloseTo(1, 5);
  });

  it("agrees with an independent implementation across the domain palette", () => {
    for (const domain of DOMAINS) {
      const s = resolveSurface({ domain });
      expect(contrastRatio(s.labelColor, s.fill), domain).toBeCloseTo(
        contrast(s.labelColor, s.fill),
        6,
      );
    }
  });
});

describe("labelColor", () => {
  it("swaps to the dark fill when the accent can't carry small text, keeping the vivid spine", () => {
    const eldritch = resolveSurface({ domain: "eldritch" });
    expect(eldritch.accent).toBe("9FD117"); // spine keeps the bright green
    expect(contrast(eldritch.accent, eldritch.fill)).toBeLessThan(AA_SMALL_TEXT);
    expect(eldritch.labelColor).toBe("46600A");
    expect(contrast(eldritch.labelColor, eldritch.fill)).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
  });

  it("keeps an accent that already reads on its own fill", () => {
    const blood = resolveSurface({ domain: "blood" });
    expect(contrast(blood.accent, blood.fill)).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
    expect(blood.labelColor).toBe(blood.accent);
  });

  // The regression this rule replaced: these read as "dark enough" on a plain brightness average, yet
  // wash out on their own pale fill. Brightness can't decide legibility — contrast can.
  it("darkens the mid-brightness accents a brightness threshold used to let through", () => {
    for (const domain of ["fire", "life", "spirit", "force", "planar", "time", "aether"] as const) {
      const s = resolveSurface({ domain });
      expect(s.labelColor, domain).not.toBe(s.accent);
      expect(contrast(s.labelColor, s.fill), domain).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
    }
  });

  it("darkens the light-on-accent domains", () => {
    for (const domain of ["air", "light", "celestial", "eldritch"] as const) {
      const s = resolveSurface({ domain });
      expect(s.labelColor, domain).not.toBe(s.accent);
    }
  });

  it("darkens the two role labels that used to fail on their own fill", () => {
    expect(resolveSurface({ role: "warning" }).labelColor).toBe("92400E");
    expect(resolveSurface({ role: "insight" }).labelColor).toBe("155E75");
  });

  it("carries a legible label color on the neutral and plain-table surfaces", () => {
    for (const [name, s] of [
      ["neutralSurface", neutralSurface()],
      ["tableSurface", tableSurface()],
    ] as const) {
      expect(s.labelColor, name).toMatch(HEX);
      expect(contrast(s.labelColor, s.fill), name).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
    }
  });
});

describe("resolveSurface — intensity ladder", () => {
  it("widens the spine as intensity rises", () => {
    expect(resolveSurface({ domain: "fire", intensity: "subtle" }).leftBorderSize).toBe(8);
    expect(resolveSurface({ domain: "fire" }).leftBorderSize).toBe(16); // default = standard
    expect(resolveSurface({ domain: "fire", intensity: "standard" }).leftBorderSize).toBe(16);
    expect(resolveSurface({ domain: "fire", intensity: "strong" }).leftBorderSize).toBe(24);
    expect(resolveSurface({ domain: "fire", intensity: "apex" }).leftBorderSize).toBe(32);
  });

  it("darkens the band monotonically while the body fill stays pale", () => {
    const standard = resolveSurface({ domain: "fire" });
    const strong = resolveSurface({ domain: "fire", intensity: "strong" });
    const apex = resolveSurface({ domain: "fire", intensity: "apex" });
    expect(strong.headerFill).toBe("7C2D12"); // the domain's darkFill
    expect(lum(strong.headerFill)).toBeLessThan(lum(standard.headerFill));
    expect(lum(apex.headerFill)).toBeLessThanOrEqual(lum(strong.headerFill));
    // body stays the pale tint at every intensity, so the description text keeps its contrast
    for (const s of [standard, strong, apex]) {
      expect(s.fill).toBe(standard.fill);
      expect(s.text).toBe("111827");
    }
  });
});

describe("resolveSurface — purity", () => {
  it("is deterministic and never leaks one resolution into the next", () => {
    const a = resolveSurface({ role: "insight", domain: "fire", intensity: "apex" });
    const b = resolveSurface({ role: "insight", domain: "fire", intensity: "apex" });
    expect(a).toEqual(b);
    // an apex resolution must not mutate the shared style maps behind a later standard one
    expect(resolveSurface({ domain: "fire" }).headerFill).toBe("D23A17");
    expect(resolveSurface({ role: "insight" }).headerFill).toBe(
      resolveSurface({ role: "insight" }).headerFill,
    );
  });
});

describe("formatInterfaceHeader — new roles", () => {
  it("formats the level-up, skill, and sheet roles", () => {
    expect(formatInterfaceHeader({ role: "levelup" })).toBe("[ LEVELUP ]");
    expect(formatInterfaceHeader({ role: "skill", domain: "fire" })).toBe("[ SKILL ] FIRE");
    expect(formatInterfaceHeader({ role: "sheet" })).toBe("[ SHEET ]");
  });

  it("returns a header for every role, with or without a domain", () => {
    for (const role of ROLES) {
      expect(formatInterfaceHeader({ role }), role).toContain(role.toUpperCase());
      expect(formatInterfaceHeader({ role, domain: "void" }), role).toContain("VOID");
    }
  });
});
