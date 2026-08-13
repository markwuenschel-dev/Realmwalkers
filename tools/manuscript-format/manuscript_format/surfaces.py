"""Port of ``frontend/src/desk/lib/litrpgSurfaces.ts`` — the LitRPG colour system.

A ``Surface`` is the resolved colour set for one panel: spine accent, body fill, header band,
border, and the two text colours picked for legibility against them. ``resolve_surface`` merges
role → creature → domain styles, then applies the intensity modifier.
"""

from __future__ import annotations

from dataclasses import dataclass

from .prose import InterfaceSpec


class PALETTE:
    ink = "111827"
    paper = "FFFFFF"
    slate = "475569"
    border = "CBD5E1"
    pale = "F8FAFC"

    red = "991B1B"
    crimson = "7F1D1D"
    amber = "B7791F"
    gold = "C7A64A"
    green = "2F7D57"
    emerald = "047857"
    blue = "2563EB"
    cyan = "008EA6"
    violet = "6D28D9"
    purple = "5B3A83"
    bronze = "A16207"
    charcoal = "1F2937"
    black = "0B0F14"

    steel = "64748B"
    steel_blue = "475569"
    brown = "78350F"
    ochre = "92400E"
    rust = "9A3412"
    ivory = "FFFBEB"
    bone = "E7E5E4"
    teal = "0F766E"
    magenta = "86198F"
    pearl = "ECFDF5"
    static_gray = "374151"


@dataclass(frozen=True)
class StyleMap:
    accent: str
    fill: str
    border: str
    header_fill: str
    dark_fill: str


@dataclass(frozen=True)
class Surface:
    accent: str
    fill: str
    header_fill: str
    border: str
    text: str
    header_text: str
    #: Accent, darkened when the raw accent is too light to read as label text on the body fill.
    label_color: str
    left_border_size: int


def _s(accent: str, fill: str, header_fill: str, dark_fill: str, border: str = PALETTE.border) -> StyleMap:
    return StyleMap(accent=accent, fill=fill, border=border, header_fill=header_fill, dark_fill=dark_fill)


ROLE_STYLES: dict[str, StyleMap] = {
    # Soft steel-blue readout card, matching the one-line interface reference.
    "system": _s("46546E", "EEF2F6", "D9E1EA", "2F3E55", border="C9D2DD"),
    "warning": _s(PALETTE.amber, "FFFBEB", PALETTE.amber, "92400E"),
    "combat": _s(PALETTE.crimson, "FEF2F2", PALETTE.crimson, "450A0A"),
    "damage": _s(PALETTE.red, "FEF2F2", PALETTE.red, "7F1D1D"),
    "healing": _s(PALETTE.green, "ECFDF5", PALETTE.green, "065F46"),
    "defense": _s(PALETTE.steel_blue, "F1F5F9", PALETTE.steel_blue, PALETTE.charcoal),
    "resource": _s(PALETTE.steel, "F8FAFC", PALETTE.steel, PALETTE.charcoal),
    "progression": _s(PALETTE.violet, "F5F3FF", PALETTE.violet, "4C1D95"),
    "xp": _s(PALETTE.emerald, "ECFDF5", PALETTE.emerald, "064E3B"),
    "crafting": _s(PALETTE.bronze, "FFFBEB", PALETTE.bronze, "78350F"),
    "insight": _s(PALETTE.cyan, "ECFEFF", PALETTE.cyan, "155E75"),
    "corruption": _s(PALETTE.magenta, "FAF5FF", PALETTE.purple, PALETTE.black),
    "name": _s(PALETTE.purple, "F5F3FF", PALETTE.purple, "3B0764"),
    "vow": _s(PALETTE.purple, "FAF5FF", PALETTE.purple, "4C1D95"),
    "item": _s(PALETTE.gold, "FFFBEB", PALETTE.gold, "854D0E"),
    # levelup / sheet render via their own fixed palettes (GOLD banner, amber SHEET in
    # render_reader.py) and never merge a role surface; skill is always domain= driven. These
    # entries exist only to keep ROLE_STYLES total over the role enum and give resolve_surface a
    # sane fallback — gold for the celebratory pair, neutral slate for a domain-less skill event.
    "levelup": _s(PALETTE.gold, "FFFDF4", "B8901C", "6E4E12"),
    "skill": _s(PALETTE.slate, "F1F5F9", PALETTE.charcoal, PALETTE.charcoal),
    "sheet": _s(PALETTE.gold, "FFFDF4", "E5B52A", "5A3F0E"),
}

# 21-domain palette, tuned as a colour wheel. accent = spine + label identity; fill = body tint;
# header_fill = coloured band (creatures / strong intensity); dark_fill = apex band.
DOMAIN_STYLES: dict[str, StyleMap] = {
    "fire": _s("D23A17", "FFF3EE", "D23A17", "7C2D12"),
    "water": _s("1C47C4", "EEF2FE", "1C47C4", "152C7A"),
    "air": _s("A9C0CC", "F5F9FB", "6E8794", "3A4A54"),
    "earth": _s("6B4223", "FBF3E9", "6B4223", "3A2413"),
    "light": _s("E5B52A", "FFFBEC", "C79418", "6E4E12"),
    "shadow": _s("7C3AED", "F6F2FE", "6D28D9", "3B1580"),
    "life": _s("1A9D3F", "ECFCEF", "1A9D3F", "0C5A28"),
    "death": _s("161C26", "F2F3F5", "161C26", "0B0F14"),
    "runic": _s("B81D94", "FDF0FA", "B81D94", "5A0E48"),
    "blood": _s("8A1020", "FCEFF0", "8A1020", "4C0810"),
    "spirit": _s("12B3A6", "EDFBF9", "0E9488", "0A5751"),
    "mind": _s("3730C4", "EEEEFC", "3730C4", "1E1A70"),
    "force": _s("C08A1E", "FDF6E8", "9C6E12", "5E4310"),
    "chaos": _s("F59310", "FFF6E8", "C7740A", "6E4008"),
    "celestial": _s("B99A4A", "FFFEF7", "B99A4A", "6E5E2E"),
    "void": _s("2A0A52", "F3F0FA", "160430", "12042A"),
    "planar": _s("7A86C8", "F1F2FB", "545FA0", "2E3670"),
    "time": _s("9C6B2E", "FBF4EA", "7A5220", "4A3216"),
    "entropy": _s("6D7355", "F4F5EF", "51563E", "34382A"),
    "eldritch": _s("9FD117", "F6FCE4", "5E7E0E", "46600A"),
    "aether": _s("0CB8D4", "EAFBFE", "0A93AB", "064E5C"),
}

CREATURE_STYLES: dict[str, StyleMap] = {
    "mortal": _s(PALETTE.slate, "FAFAF9", PALETTE.slate, PALETTE.charcoal),
    "beast": _s(PALETTE.ochre, "FFFBEB", PALETTE.brown, "451A03"),
    "monster": _s(PALETTE.rust, "FFF7ED", PALETTE.rust, "7C2D12"),
    "demon": _s(PALETTE.crimson, "FEF2F2", PALETTE.crimson, "450A0A"),
    "archdemon": _s(PALETTE.crimson, "FEF2F2", PALETTE.black, PALETTE.black),
    "angel": _s(PALETTE.gold, PALETTE.ivory, "D97706", "78350F"),
    "archangel": _s(PALETTE.gold, PALETTE.ivory, "FDE68A", "78350F"),
    "undead": _s(PALETTE.bone, "F5F5F4", PALETTE.charcoal, PALETTE.black),
    "dragon": _s(PALETTE.bronze, "FFF7ED", "B45309", "78350F"),
    "construct": _s(PALETTE.steel_blue, "F1F5F9", PALETTE.steel, PALETTE.charcoal),
    "spirit": _s(PALETTE.teal, "F0FDFA", PALETTE.teal, "134E4A"),
    "fae": _s(PALETTE.green, "F0FDF4", PALETTE.violet, "065F46"),
    "celestial": _s(PALETTE.gold, PALETTE.ivory, "FDE68A", "78350F"),
    "voidborn": _s("4C1D95", "F5F3FF", PALETTE.black, PALETTE.black),
    "eldritch": _s(PALETTE.purple, "FAF5FF", PALETTE.black, PALETTE.black),
    "xyloryn": _s("84CC16", PALETTE.pearl, PALETTE.black, PALETTE.black),
    "nhal": _s(PALETTE.static_gray, "F3F4F6", PALETTE.black, PALETTE.black),
}


def _luminance(hex_color: str) -> float:
    clean = hex_color.replace("#", "")
    r = int(clean[0:2], 16)
    g = int(clean[2:4], 16)
    b = int(clean[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def readable_text(hex_color: str) -> str:
    return "FFFFFF" if _luminance(hex_color) < 0.55 else "111827"


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance — the measure that actually answers 'is this readable on that?'."""
    clean = hex_color.replace("#", "")
    chans = []
    for i in (0, 2, 4):
        v = int(clean[i : i + 2], 16) / 255
        chans.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = chans
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours: 1 (identical) … 21 (black on white)."""
    hi, lo = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


#: WCAG AA for small text — interface labels render at 7.5–9.5pt, so this is the bar that applies.
AA_SMALL_TEXT = 4.5


def _first_legible(candidates: list[str], bg: str) -> str:
    """First candidate legible on ``bg``; ink is the always-readable backstop on any pale fill."""
    for c in candidates:
        if contrast_ratio(c, bg) >= AA_SMALL_TEXT:
            return c
    return PALETTE.ink


def _merge(base: StyleMap, overlay: StyleMap) -> StyleMap:
    return StyleMap(
        accent=overlay.accent or base.accent,
        fill=overlay.fill or base.fill,
        border=overlay.border or base.border,
        header_fill=overlay.header_fill or base.header_fill,
        dark_fill=overlay.dark_fill or base.dark_fill,
    )


def _darkest(a: str, b: str) -> str:
    return a if _luminance(a) <= _luminance(b) else b


def resolve_surface(spec: InterfaceSpec | None = None) -> Surface:
    """Merge role → creature → domain, then apply the intensity modifier."""
    spec = spec or InterfaceSpec()
    merged = ROLE_STYLES.get(spec.role or "system", ROLE_STYLES["system"])

    if spec.creature and spec.creature in CREATURE_STYLES:
        merged = _merge(merged, CREATURE_STYLES[spec.creature])

    if spec.domain and spec.domain in DOMAIN_STYLES:
        domain = DOMAIN_STYLES[spec.domain]
        if spec.creature:
            # A domain-flavoured creature keeps its bestiary card, tinted by the domain accent.
            merged = StyleMap(
                accent=domain.accent,
                fill=merged.fill,
                border=domain.border,
                header_fill=merged.header_fill,
                dark_fill=merged.dark_fill,
            )
        else:
            # Pure magic block: the domain owns the whole surface (tint + spine + band).
            merged = _merge(merged, domain)

    intensity = spec.intensity or "standard"
    left_border_size = 16
    header_fill = merged.header_fill

    if intensity == "subtle":
        left_border_size = 8
    elif intensity == "strong":
        left_border_size = 24
        header_fill = merged.dark_fill
    elif intensity == "apex":
        left_border_size = 32
        header_fill = _darkest(merged.dark_fill, _darkest(merged.header_fill, merged.accent))

    fill = merged.fill
    # The spine keeps the true (possibly bright) accent; the label text falls back to the dark fill
    # when the accent can't carry small caps on the body tint, and to ink if even that is too close.
    label_color = _first_legible([merged.accent, merged.dark_fill], fill)

    return Surface(
        accent=merged.accent,
        fill=fill,
        header_fill=header_fill,
        border=merged.border,
        text=readable_text(fill),
        header_text=readable_text(header_fill),
        label_color=label_color,
        left_border_size=left_border_size,
    )


def neutral_surface() -> Surface:
    return Surface(
        accent=PALETTE.slate,
        fill="F3F4F6",
        header_fill=PALETTE.charcoal,
        border=PALETTE.border,
        text=PALETTE.ink,
        header_text="FFFFFF",
        label_color=PALETTE.slate,
        left_border_size=16,
    )


def table_surface() -> Surface:
    return Surface(
        accent=PALETTE.slate,
        fill=PALETTE.paper,
        header_fill=PALETTE.charcoal,
        border=PALETTE.border,
        text=PALETTE.ink,
        header_text="FFFFFF",
        label_color=PALETTE.slate,
        left_border_size=12,
    )


_PROGRESSION_ROLES = frozenset({"progression", "xp", "crafting"})


def format_interface_header(spec: InterfaceSpec) -> str:
    """Uppercase display header shared by Reader DOCX and Shunn plain text."""
    if spec.creature == "nhal":
        return "[ WARNING ] CREATURE SCAN · N'HAL"

    role = (spec.role or "interface").upper()

    if spec.creature:
        header = f"[ {role} ] CREATURE SCAN · {spec.creature.upper()}"
        if spec.domain:
            header += f" · {spec.domain.upper()}"
        return header

    if spec.domain:
        if spec.role and spec.role in _PROGRESSION_ROLES:
            return f"[ {role} ] PROGRESSION · {spec.domain.upper()}"
        return f"[ {role} ] {spec.domain.upper()}"

    return f"[ {role} ]"


def format_interface_shunn_header(spec: InterfaceSpec) -> str:
    return format_interface_header(spec)
