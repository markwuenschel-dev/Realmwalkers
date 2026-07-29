# Wren Calloway — Character Reference

A reference document, not a novel. Rendered with `--to doc`, which sends the markdown
straight through the block parser with no book format and no `beautify()` pre-pass —
so directive attributes survive verbatim.

## Sheet

@style role=sheet name="Wren Calloway" age=24 level=7
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
| Sparkwhip · Shadow Step · Kindle · Ash Guard | | |

## Disciplines

@style domain=fire skill="Sparkwhip" tier=III
| Cost | Range | Burn |
|---|---|---|
| 24 vigor | 8 m | +2/s |

## Notes

> [!IMPORTANT]
> Corruption resistance is the gate on everything below tier IV.

Plain prose with `inline code`, **bold**, *emphasis*, and
[a link](https://example.com) all render inline.

- First bullet
- Second bullet

1. First numbered item
2. Second numbered item

---

```
@interface role=warning intensity=apex
Corruption threshold exceeded. Further exposure is not survivable.
```
