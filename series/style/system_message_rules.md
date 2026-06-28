# System Message Rules — Dominion Realm

> **Purpose:** Defines how the LitRPG interface — notifications, stats, skill panels, warnings, level-ups — appears in the prose. The UI is a translation layer, not a game. Its formatting must reflect that.

---

## Core Principle

The interface is not the story. It is Marcus's implant rendering incomprehensible metaphysical structures into data his mind can process. It is imperfect, intermittent, occasionally wrong, and always subordinate to the scene it interrupts.

The UI does not pause the narrative. It intrudes on it.

---

## The Arc of UI Presence

### Early Story — High Presence
When Marcus first arrives in the Realm, the interface is active and frequent. He is learning to read it, learning what it means, learning when to trust it. Notifications fire at unexpected moments. He doesn't always understand them. Some appear to be errors or mistranslations.

At this stage: UI appears several times per chapter. It can interrupt scenes, arrive mid-action, surface during emotional moments. Its presence is part of the texture of the early Realm experience.

### Mid Story — Decreasing Presence
As Marcus internalizes Realm logic, he needs the interface less. It becomes background. He has learned to read the underlying patterns without needing the translation layer to spell everything out.

At this stage: UI appears for significant changes only — new abilities, meaningful warnings, things the interface catches that he missed. It does not narrate the fight. It notes the outcome.

### Late Story — Sparse and Significant
By late story, a UI notification carries weight precisely because it is rare. When the interface surfaces, something has changed that Marcus couldn't have anticipated. Its appearance is itself information.

At this stage: UI appears for major events, threshold crossings, anomalies the implant registers that the narrative hasn't explained yet.

---

## Formatting

### Standard Notification
Used for skill unlocks, status changes, warnings, basic system messages.

```
[ SYSTEM ]
Ocular Response: Threat detection elevated.
Emotional state: Controlled fear.
Recommendation: Disengage or escalate.
```

Rules:
- All caps for the source tag: `[ SYSTEM ]`, `[ WARNING ]`, `[ INTERFACE ]`
- Sentence case for the content lines
- No bullet points inside system messages — line breaks only
- Three lines maximum for standard notifications; longer entries use the Panel format
- No exclamation points. The system is not excited.

### Warning / Alert
Used when the interface flags something urgent.

```
[ WARNING ]
Biological signature: Unknown classification.
Threat assessment: Insufficient data.
Proceed with caution.
```

### Skill / Ability Notification
Used when a new ability activates, evolves, or reaches a new stage.

```
[ INTERFACE ]
Ocular Stage Advancement: Limbal Shift → Iris Refraction
New capability: Neuro-optical fibers active.
Cost threshold: Elevated. Monitor resource expenditure.
```

### Stat Panel
Used sparingly — early story only, during moments when Marcus is actively consulting the interface rather than receiving an interruption. Formatted as a discrete block, clearly separated from prose.

```
┌─────────────────────────────────┐
│ CURRENT STATUS                  │
│                                 │
│ Ocular Stage:  1 — Limbal Shift │
│ Reserve:       67%              │
│ Emotional:     Controlled fear  │
│ Active:        Threat detection │
└─────────────────────────────────┘
```

Rules:
- Stat panels appear only when Marcus deliberately consults the interface
- Never mid-combat — he doesn't have time
- Maximum one per chapter in early story; zero in mid-to-late story
- Do not use for flavor; only when the content of the stats matters to a decision he's making

### Illyri's Interface Voice
When Illyri communicates through the implant, her messages are formatted differently from system notifications — they feel older, less precise, slightly off-center.

```
[ ILLYRI ]
That thing has a name. I knew it once.
Ask me again when I remember it.
```

Rules:
- Sentence fragments permitted in Illyri's messages — she does not complete thoughts she doesn't have
- Her messages can be cryptic; they should never be decorative
- Do not italicize or stylize her text beyond the tag format — the strangeness is in the content, not the formatting

---

## Prose Integration

System messages interrupt the prose. They do not float separately from it. The scene continues around them.

**Wrong approach:**
> Marcus raised his sword. The creature lunged.
>
> *[SYSTEM: Threat detected. Recommend evasion.]*
>
> He stepped aside.

The UI break kills momentum and treats the notification as a pause button.

**Correct approach:**
> Marcus raised his sword. The creature lunged — and then the interface fired across his vision, cold and clinical.
>
> ```
> [ WARNING ]
> Biological signature: Adapting.
> Previous evasion pattern: Logged.
> ```
>
> It had already adjusted for him. He moved anyway, differently this time, without knowing why.

The UI is part of the scene. It creates information the character acts on. The prose doesn't stop for it.

---

## What the UI Does Not Do

- Deliver exposition the story hasn't earned yet
- Explain the Realm's lore in system message format
- Level Marcus up in a way that feels like a video game reward
- Interrupt emotional scenes with mechanical data unless the interruption is itself the point
- Appear so frequently in late story that it loses meaning
- Use excited language, dramatic formatting, or aesthetic flair — the system is functional, not dramatic
- Be correct all the time — the interface mistranslates, misclassifies, and occasionally fails to render something at all

---

## Mistranslation and Failure

The interface is imperfect. It was designed by humans who didn't fully understand what they were building, shaped by nonhuman knowledge for purposes Astria's engineers didn't grasp. It can:

- Fail to classify something it hasn't encountered before
- Render a Realm concept in an Earth framework that doesn't quite fit
- Produce a warning with no actionable information
- Go silent at the wrong moment
- Display something that seems like an error but isn't

When the interface fails or mistranslates, that is a story event. It tells Marcus — and the reader — that he has encountered something outside the implant's model of the Realm. This is always significant.

---

## Quick Reference

| Type | Tag | Max Length | When |
|---|---|---|---|
| Standard notification | `[ SYSTEM ]` | 3 lines | Status changes, passive updates |
| Warning | `[ WARNING ]` | 3 lines | Urgent flags, threat detection |
| Ability change | `[ INTERFACE ]` | 4 lines | Stage advancement, new capability |
| Stat panel | `STATUS` (boxed) | 6 lines | Early story, deliberate consultation only |
| Illyri message | `[ ILLYRI ]` | 2–3 lines | Her communication through the implant |

---

*Cross-reference: `voice_guide.md` for the Realm rendering philosophy. `prose_contract.md` rule 6. `style_examples.md` for applied UI-in-prose examples.*

*Last updated: working draft*
