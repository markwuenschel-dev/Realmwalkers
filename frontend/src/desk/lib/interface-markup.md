# LitRPG interface markup — authoring reference

Copy-paste examples for every styled panel the Reader DOCX exporter renders. All directives reuse one
`key=value` attribute grammar. Values may be **bare** (`role=skill`) or **double-quoted** to include
spaces (`name="Kaelen Voss"`). Any omitted attribute or line group degrades gracefully — no band, no
footnote, no grid.

- `@interface …` lines live **inside a fenced ```` ``` ```` block** (first line of the fence).
- `@style …` is a **standalone line immediately above** a stat window or pipe table it color-codes.
- Color comes from `domain=` (21 domains). Deltas: `->` or `→` between two values → old → new,
  green on a gain / red on a loss (sign-detected).

---

## Attribute quick reference

| Attribute | Used by | Meaning |
|-----------|---------|---------|
| `role` | all | `system` `warning` `combat` … `levelup` `skill` `sheet` (see roles below) |
| `domain` | magic, skill, creature, `@style` | one of the 21 magic domains — drives palette |
| `creature` | creature scan | creature kind → bestiary card |
| `intensity` | creature, magic | `subtle` `standard` `strong` `apex` (creature threat is derived from this) |
| `skill` | magic, skill, creature | display name (quote if it has spaces) |
| `tier` | magic, skill | tier label, e.g. `III` |
| `name` | levelup, sheet | subject name |
| `from` / `to` | levelup | prior / new level (big `6 → 7` numerals) |
| `rank` | skill | proficiency rank, e.g. `Novice` |
| `via` | skill | how it was earned → italic footnote |
| `age` / `level` | sheet | identity-band fields |

---

## System / role message
Elegant centered label under a hairline rule, serif body.

````
```
@interface role=system
You have entered the **Sunken Vault of Aszhar**. Ambient mana density is high;
your corruption resistance is being tested.
```
````

## Magic block
Domain-colored header band (`skill · domain · tier`), tinted description body.

````
```
@interface domain=fire skill="Emberlash" tier=III
A whip of living flame that hungers for what it touches; each strike deepens the burn.
```
````

## Creature scan → bestiary card
Colored name band + `Bestiary` tag, ruled `KIND · THREAT · DOMAIN` strip (threat from `intensity`),
tinted description.

````
```
@interface creature=monster domain=blood intensity=strong skill="Gravemaw Broodmother"
A bloated arachnid queen whose egg-sacs glow with stolen soul-light. She does not
chase — she waits, and the vault does the chasing for her.
```
````

## Level-up banner
Loud dark gold band (`LEVEL UP` + `from → to`). Prose lines = the announcement (lead the banner).
`- Label: old -> new` lines = the vitals-growth grid. Skills are **not** granted here — they come from
use (see next).

````
```
@interface role=levelup name="Kaelen Voss" from=6 to=7
You have reached **Level 7**. As a Human, your ambition outpaces your years —
you gain **4 free attribute points** to spend. Choose wisely.
- Health: 72 -> 84
- Mana: 37 -> 45
- Stamina: 55 -> 60
```
````

## Skill learned (through use)
Domain-coded acquisition band (`SKILL LEARNED · <domain>` + `rank · tier` tag), description, and a
`via=` footnote. Use this — not level-up — for skill acquisition.

````
```
@interface role=skill domain=fire skill="Emberlash" tier=I rank="Novice" via="200 successful casts under strain"
Repetition has taught your hand what instruction never could — the flame now
answers as an extension of your will.
```
````

## Character sheet
`@style role=sheet …` on the line **above** a pipe table. Identity band comes from `name/age/level`.
Inside the table:
- `| # STATS |` (single cell starting `#`) → a gold section band spanning all columns.
- `~domain ` at the start of a cell → a colored domain pip (■) before the text.
- `Label: value` cells auto-style the label as small-caps.

````
@style role=sheet name="Kaelen Voss" age=24 level=7
| Race: Human | Discipline: Pyromancer | Language: Common |
|---|---|---|
| Prestige: 2 | Focus: Flame | Alignment: Neutral |
| # STATS | | |
| Health: 84 | Mana: 45 | Stamina: 60 |
| # SPELL POWER BONUSES | | |
| ~fire Fire +40% | ~light Light +18% | ~air Air +9% |
| ~life Life +6% | ~blood Blood +15% | ~earth Earth +3% |
| # RESISTANCES | | |
| ~fire Fire 40% | ~air Air 10% | ~earth Earth 15% |
| # ABILITIES | | |
| Emberlash · Umbral Step · Flamecall · Ward of Ash | | |
````

## Color-coding a stat window or plain table
`@style` (no `role=sheet`) with a `domain=` tints any following stat window / pipe table — spanning
band (`skill · domain · tier` + `STATS`/`BESTIARY` tag), colored header, domain spine.

````
@style domain=fire skill="Emberlash" tier=III
| Cost | Range | Burn |
|---|---|---|
| 24 vigor | 8 m | +2/s |
````

---

### The 21 domains
`fire water air earth light shadow life death runic blood spirit mind force chaos celestial void
planar time entropy eldritch aether`

Each has a tuned accent (spine + label), a pale body tint, and a strong band color. The spine always
keeps the vivid accent. Label text is checked against the body tint it sits on and drops to the domain's
dark fill whenever the accent can't carry small caps at readable contrast (WCAG AA, 4.5:1) — so a bright
`aether` cyan or a washed-out `force` gold reads as dark teal / dark bronze in the text while the spine
stays vivid. No hand-maintained list: the check runs per surface, and the test suite enforces it across
every role × domain × creature × intensity combination.

### Fonts
- **Labels:** Bahnschrift (ships with Windows 10+/Office — no embedding; Franklin Gothic fallback).
- **Body:** Georgia (Word-native serif).
- To force-embed a custom label font, add its TTFs to `lib/fonts.ts` → `LABEL_FONTS`.
