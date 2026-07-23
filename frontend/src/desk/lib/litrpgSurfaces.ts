import type { CreatureKind, InterfaceSpec, MagicDomain, UiRole } from "../prose";

export const PALETTE = {
  ink: "111827",
  paper: "FFFFFF",
  slate: "475569",
  border: "CBD5E1",
  pale: "F8FAFC",

  red: "991B1B",
  crimson: "7F1D1D",
  amber: "B7791F",
  gold: "C7A64A",
  green: "2F7D57",
  emerald: "047857",
  blue: "2563EB",
  cyan: "008EA6",
  violet: "6D28D9",
  purple: "5B3A83",
  bronze: "A16207",
  charcoal: "1F2937",
  black: "0B0F14",

  steel: "64748B",
  steelBlue: "475569",
  brown: "78350F",
  ochre: "92400E",
  rust: "9A3412",
  ivory: "FFFBEB",
  bone: "E7E5E4",
  teal: "0F766E",
  magenta: "86198F",
  pearl: "ECFDF5",
  staticGray: "374151",
} as const;

type StyleMap = {
  accent: string;
  fill: string;
  border: string;
  headerFill: string;
  darkFill: string;
};

export type Surface = {
  accent: string;
  fill: string;
  headerFill: string;
  border: string;
  text: string;
  headerText: string;
  /** Accent, darkened when the raw accent is too light to read as label text on the body fill. */
  labelColor: string;
  leftBorderSize: number;
};

function style(
  accent: string,
  fill: string,
  headerFill: string,
  darkFill: string,
  border = PALETTE.border,
): StyleMap {
  return { accent, fill, border, headerFill, darkFill };
}

const ROLE_STYLES: Record<UiRole, StyleMap> = {
  system: style(PALETTE.slate, "F1F5F9", PALETTE.charcoal, PALETTE.charcoal),
  warning: style(PALETTE.amber, "FFFBEB", PALETTE.amber, "92400E"),
  combat: style(PALETTE.crimson, "FEF2F2", PALETTE.crimson, "450A0A"),
  damage: style(PALETTE.red, "FEF2F2", PALETTE.red, "7F1D1D"),
  healing: style(PALETTE.green, "ECFDF5", PALETTE.green, "065F46"),
  defense: style(PALETTE.steelBlue, "F1F5F9", PALETTE.steelBlue, PALETTE.charcoal),
  resource: style(PALETTE.steel, "F8FAFC", PALETTE.steel, PALETTE.charcoal),
  progression: style(PALETTE.violet, "F5F3FF", PALETTE.violet, "4C1D95"),
  xp: style(PALETTE.emerald, "ECFDF5", PALETTE.emerald, "064E3B"),
  crafting: style(PALETTE.bronze, "FFFBEB", PALETTE.bronze, "78350F"),
  insight: style(PALETTE.cyan, "ECFEFF", PALETTE.cyan, "155E75"),
  corruption: style(PALETTE.magenta, "FAF5FF", PALETTE.purple, PALETTE.black),
  name: style(PALETTE.purple, "F5F3FF", PALETTE.purple, "3B0764"),
  vow: style(PALETTE.purple, "FAF5FF", PALETTE.purple, "4C1D95"),
  item: style(PALETTE.gold, "FFFBEB", PALETTE.gold, "854D0E"),
  // levelup / sheet render via their own fixed palettes (GOLD banner, amber SHEET in docx.ts) and
  // never merge a role surface; skill is always domain= driven. These entries exist only to keep
  // ROLE_STYLES total over UiRole and give resolveSurface a sane fallback — gold for the celebratory
  // pair, neutral slate for a domain-less skill event.
  levelup: style(PALETTE.gold, "FFFDF4", "B8901C", "6E4E12"),
  skill: style(PALETTE.slate, "F1F5F9", PALETTE.charcoal, PALETTE.charcoal),
  sheet: style(PALETTE.gold, "FFFDF4", "E5B52A", "5A3F0E"),
};

// 21-domain palette, tuned as a color wheel: warm (fire/blood/chaos/light/celestial/time/earth/force),
// green (life/eldritch/entropy), cyan-blue (spirit/aether/water/air/planar), violet-magenta
// (mind/shadow/runic/void), and neutral anchors (death). accent = spine + label identity; fill = body
// tint; headerFill = colored band (creatures / strong intensity); darkFill = apex band.
const DOMAIN_STYLES: Record<MagicDomain, StyleMap> = {
  fire: style("D23A17", "FFF3EE", "D23A17", "7C2D12"),
  water: style("1C47C4", "EEF2FE", "1C47C4", "152C7A"),
  air: style("A9C0CC", "F5F9FB", "6E8794", "3A4A54"),
  earth: style("6B4223", "FBF3E9", "6B4223", "3A2413"),
  light: style("E5B52A", "FFFBEC", "C79418", "6E4E12"),
  shadow: style("7C3AED", "F6F2FE", "6D28D9", "3B1580"),
  life: style("1A9D3F", "ECFCEF", "1A9D3F", "0C5A28"),
  death: style("161C26", "F2F3F5", "161C26", "0B0F14"),
  runic: style("B81D94", "FDF0FA", "B81D94", "5A0E48"),
  blood: style("8A1020", "FCEFF0", "8A1020", "4C0810"),
  spirit: style("12B3A6", "EDFBF9", "0E9488", "0A5751"),
  mind: style("3730C4", "EEEEFC", "3730C4", "1E1A70"),
  force: style("C08A1E", "FDF6E8", "9C6E12", "5E4310"),
  chaos: style("F59310", "FFF6E8", "C7740A", "6E4008"),
  celestial: style("B99A4A", "FFFEF7", "B99A4A", "6E5E2E"),
  void: style("2A0A52", "F3F0FA", "160430", "12042A"),
  planar: style("7A86C8", "F1F2FB", "545FA0", "2E3670"),
  time: style("9C6B2E", "FBF4EA", "7A5220", "4A3216"),
  entropy: style("6D7355", "F4F5EF", "51563E", "34382A"),
  eldritch: style("9FD117", "F6FCE4", "5E7E0E", "46600A"),
  aether: style("0CB8D4", "EAFBFE", "0A93AB", "064E5C"),
};

const CREATURE_STYLES: Record<CreatureKind, StyleMap> = {
  mortal: style(PALETTE.slate, "FAFAF9", PALETTE.slate, PALETTE.charcoal),
  beast: style(PALETTE.ochre, "FFFBEB", PALETTE.brown, "451A03"),
  monster: style(PALETTE.rust, "FFF7ED", PALETTE.rust, "7C2D12"),
  demon: style(PALETTE.crimson, "FEF2F2", PALETTE.crimson, "450A0A"),
  archdemon: style(PALETTE.crimson, "FEF2F2", PALETTE.black, PALETTE.black),
  angel: style(PALETTE.gold, PALETTE.ivory, "D97706", "78350F"),
  archangel: style(PALETTE.gold, PALETTE.ivory, "FDE68A", "78350F"),
  undead: style(PALETTE.bone, "F5F5F4", PALETTE.charcoal, PALETTE.black),
  dragon: style(PALETTE.bronze, "FFF7ED", "B45309", "78350F"),
  construct: style(PALETTE.steelBlue, "F1F5F9", PALETTE.steel, PALETTE.charcoal),
  spirit: style(PALETTE.teal, "F0FDFA", PALETTE.teal, "134E4A"),
  fae: style(PALETTE.green, "F0FDF4", PALETTE.violet, "065F46"),
  celestial: style(PALETTE.gold, PALETTE.ivory, "FDE68A", "78350F"),
  voidborn: style("4C1D95", "F5F3FF", PALETTE.black, PALETTE.black),
  eldritch: style(PALETTE.purple, "FAF5FF", PALETTE.black, PALETTE.black),
  xyloryn: style("84CC16", PALETTE.pearl, PALETTE.black, PALETTE.black),
  nhal: style(PALETTE.staticGray, "F3F4F6", PALETTE.black, PALETTE.black),
};

export function readableText(hex: string): "FFFFFF" | "111827" {
  return luminance(hex) < 0.55 ? "FFFFFF" : "111827";
}

function luminance(hex: string): number {
  const clean = hex.replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

// Whether a color is READABLE on a given background is a contrast question, not a brightness one —
// a saturated mid-tone green scores "dark" on the average above yet still washes out on a pale green
// fill. WCAG relative luminance + ratio is the measure that actually answers it.
function relativeLuminance(hex: string): number {
  const clean = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(clean.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two hex colors: 1 (identical) … 21 (black on white). */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** WCAG AA for small text — interface labels render at 7.5–9.5pt, so this is the bar that applies. */
export const AA_SMALL_TEXT = 4.5;

/** First candidate legible on `bg`; ink is the always-readable backstop on any pale body fill. */
function firstLegible(candidates: string[], bg: string): string {
  return candidates.find((c) => contrastRatio(c, bg) >= AA_SMALL_TEXT) ?? PALETTE.ink;
}

function mergeStyles(base: StyleMap, overlay: StyleMap): StyleMap {
  return {
    accent: overlay.accent || base.accent,
    fill: overlay.fill || base.fill,
    border: overlay.border || base.border,
    headerFill: overlay.headerFill || base.headerFill,
    darkFill: overlay.darkFill || base.darkFill,
  };
}

function darkest(a: string, b: string): string {
  return luminance(a) <= luminance(b) ? a : b;
}

export function resolveSurface(spec: InterfaceSpec = {}): Surface {
  let merged = ROLE_STYLES[spec.role ?? "system"] ?? ROLE_STYLES.system;

  if (spec.creature && CREATURE_STYLES[spec.creature]) {
    merged = mergeStyles(merged, CREATURE_STYLES[spec.creature]);
  }

  if (spec.domain && DOMAIN_STYLES[spec.domain]) {
    const domain = DOMAIN_STYLES[spec.domain];
    if (spec.creature) {
      // A domain-flavoured creature keeps its bestiary card, tinted by the domain accent.
      merged = { ...merged, accent: domain.accent, border: domain.border };
    } else {
      // Pure magic block: the domain owns the whole surface (tint + spine + band).
      merged = mergeStyles(merged, domain);
    }
  }

  const intensity = spec.intensity ?? "standard";
  let leftBorderSize = 16;
  let headerFill = merged.headerFill;

  switch (intensity) {
    case "subtle":
      leftBorderSize = 8;
      break;
    case "strong":
      leftBorderSize = 24;
      headerFill = merged.darkFill;
      break;
    case "apex":
      leftBorderSize = 32;
      headerFill = darkest(merged.darkFill, darkest(merged.headerFill, merged.accent));
      break;
  }

  // Apex/strong: dark header, pale body — body fill stays readable
  const fill = merged.fill;
  // The spine keeps the true (possibly bright) accent; the label text falls back to the dark fill when
  // the accent can't carry small caps on the body tint, and to ink if even that is too close.
  const labelColor = firstLegible([merged.accent, merged.darkFill], fill);

  return {
    accent: merged.accent,
    fill,
    headerFill,
    border: merged.border,
    text: readableText(fill),
    headerText: readableText(headerFill),
    labelColor,
    leftBorderSize,
  };
}

export function neutralSurface(): Surface {
  return {
    accent: PALETTE.slate,
    fill: "F3F4F6",
    headerFill: PALETTE.charcoal,
    border: PALETTE.border,
    text: PALETTE.ink,
    headerText: "FFFFFF",
    labelColor: PALETTE.slate,
    leftBorderSize: 16,
  };
}

export function tableSurface(): Surface {
  return {
    accent: PALETTE.slate,
    fill: PALETTE.paper,
    headerFill: PALETTE.charcoal,
    border: PALETTE.border,
    text: PALETTE.ink,
    headerText: "FFFFFF",
    labelColor: PALETTE.slate,
    leftBorderSize: 12,
  };
}

function roleLabel(role?: UiRole): string {
  return (role ?? "interface").toUpperCase();
}

function creatureLabel(creature: CreatureKind): string {
  return creature.toUpperCase();
}

function domainLabel(domain: MagicDomain): string {
  return domain.toUpperCase();
}

/** Uppercase display header shared by Reader DOCX and Shunn plain text. */
export function formatInterfaceHeader(spec: InterfaceSpec): string {
  if (spec.creature === "nhal") {
    return "[ WARNING ] CREATURE SCAN · N'HAL";
  }

  const role = roleLabel(spec.role);

  if (spec.creature) {
    let header = `[ ${role} ] CREATURE SCAN · ${creatureLabel(spec.creature)}`;
    if (spec.domain) header += ` · ${domainLabel(spec.domain)}`;
    return header;
  }

  if (spec.domain) {
    const progressionRoles = new Set<UiRole>(["progression", "xp", "crafting"]);
    if (spec.role && progressionRoles.has(spec.role)) {
      return `[ ${role} ] PROGRESSION · ${domainLabel(spec.domain)}`;
    }
    return `[ ${role} ] ${domainLabel(spec.domain)}`;
  }

  return `[ ${role} ]`;
}

export function formatInterfaceShunnHeader(spec: InterfaceSpec): string {
  return formatInterfaceHeader(spec);
}
