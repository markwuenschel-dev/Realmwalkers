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
};

const DOMAIN_STYLES: Record<MagicDomain, StyleMap> = {
  fire: style("C2410C", "FFF7ED", "EA580C", "7C2D12"),
  water: style(PALETTE.blue, "EFF6FF", PALETTE.blue, "1E3A8A"),
  air: style("94A3B8", "F8FAFC", "64748B", PALETTE.charcoal),
  earth: style(PALETTE.brown, "FEF3C7", PALETTE.brown, "451A03"),
  light: style(PALETTE.gold, PALETTE.ivory, PALETTE.gold, "854D0E"),
  shadow: style(PALETTE.violet, "F5F3FF", "5B21B6", "2E1065"),
  life: style(PALETTE.green, "ECFDF5", PALETTE.green, "065F46"),
  death: style(PALETTE.charcoal, "F3F4F6", PALETTE.black, PALETTE.black),
  runic: style(PALETTE.purple, "FAF5FF", PALETTE.purple, "3B0764"),
  blood: style(PALETTE.crimson, "FEF2F2", PALETTE.crimson, "450A0A"),
  spirit: style(PALETTE.teal, "F0FDFA", PALETTE.teal, "134E4A"),
  mind: style("6366F1", "EEF2FF", "4F46E5", "312E81"),
  force: style(PALETTE.bronze, "FFF7ED", PALETTE.bronze, "78350F"),
  chaos: style("BE123C", "FFF1F2", "9F1239", "4C0519"),
  celestial: style(PALETTE.gold, PALETTE.ivory, "D97706", "78350F"),
  void: style("4C1D95", "F5F3FF", PALETTE.black, PALETTE.black),
  planar: style("64748B", "F1F5F9", PALETTE.charcoal, PALETTE.charcoal),
  time: style("B45309", "FFFBEB", "92400E", "451A03"),
  entropy: style(PALETTE.staticGray, "F3F4F6", PALETTE.charcoal, PALETTE.black),
  eldritch: style(PALETTE.purple, "FAF5FF", PALETTE.black, PALETTE.black),
  aether: style("0284C7", "F0F9FF", "0369A1", "0C4A6E"),
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
  const clean = hex.replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance < 0.55 ? "FFFFFF" : "111827";
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
  const lum = (hex: string) => {
    const c = hex.replace("#", "");
    const r = parseInt(c.slice(0, 2), 16);
    const g = parseInt(c.slice(2, 4), 16);
    const bl = parseInt(c.slice(4, 6), 16);
    return (0.299 * r + 0.587 * g + 0.114 * bl) / 255;
  };
  return lum(a) <= lum(b) ? a : b;
}

export function resolveSurface(spec: InterfaceSpec = {}): Surface {
  let merged = ROLE_STYLES[spec.role ?? "system"] ?? ROLE_STYLES.system;

  if (spec.creature && CREATURE_STYLES[spec.creature]) {
    merged = mergeStyles(merged, CREATURE_STYLES[spec.creature]);
  }

  if (spec.domain && DOMAIN_STYLES[spec.domain]) {
    const domain = DOMAIN_STYLES[spec.domain];
    merged = {
      ...merged,
      accent: domain.accent,
      border: domain.border,
    };
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

  return {
    accent: merged.accent,
    fill,
    headerFill,
    border: merged.border,
    text: readableText(fill),
    headerText: readableText(headerFill),
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
