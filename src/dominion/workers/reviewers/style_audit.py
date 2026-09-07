"""Style audit — judge FINISHED prose against the author's own rule documents.

This is the first consumer anywhere that loads a style slug on the JUDGING side. Everything else in
the system criticises prose against a per-scene contract — beats, clauses, ledger values, declared
state changes — which makes the whole critique apparatus structurally unable to accept a paragraph
and a style guide. `push_style` has been pushing `prose_clarity_rules`, `prose_contract` and
`voice_guide` into `style_documents` all along, where nothing read them (`style_source.py:76-86` says
so outright); `forbidden_drift` reached only the drafter, in generative DRAFT mode. This module is the
reader those documents were written for.

It is deliberately NOT a `Reviewer`. The protocol is `review(scene_prose, ctx) -> list[Flag]`, and a
`SceneContext` asserts a book, a chapter, a packet and a beat that pasted prose does not have. Rather
than fabricate them, this takes a passage and a session and reads the standards itself.

THE OUTPUT SHAPE IS `Suggestion`, ON PURPOSE. `models.py:872-888` already defines
`{quote, new_text, why, status}` with a pending/accepted/rejected lifecycle, an inline renderer that
strikes the old text and underlines the new (`SceneScreen.tsx:443-479`), an accept/reject rail
(`SceneScreen.tsx:1047-1135`), and `applyAcceptedSuggestions` to fold the accepted ones in
(`format.ts:27-38`). Every one of those had no machine producer. Emitting anything else would mean
rebuilding a lifecycle that already works.

Fabrication is guarded deterministically, not trusted: `quote_is_supported` re-checks that every cited
passage actually occurs in the prose, and a finding that fails is dropped and counted. A rule citation
the author cannot find in their own text is worse than no finding at all — it makes the audit
unfalsifiable, which is exactly the failure `reviewers/base.py:117-141` was written to stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.agent_policy import quality_effort, quality_temperature
from dominion.shared.config import settings
from dominion.shared.enums import Severity
from dominion.workers.budget import TokenBudget
from dominion.workers.context.forbidden_drift import AUDIT, cast_present_in, scope_forbidden_drift
from dominion.workers.context.style_source import load_style_document
from dominion.workers.llm_escalation import complete_with_rate_limit_fallback
from dominion.workers.reviewers.base import parse_json_objects, quote_is_supported

__all__ = ["AuditFinding", "StyleAuditResult", "audit_prose"]

_AUDIT_MAX_TOKENS = 4000

# The standards, in the order they are laid before the model. `prose_clarity_rules` leads because it
# carries The One Test — a hard gate its own text says outranks every other judgement ("APPROVED is
# impossible until it is rendered, regardless of prose quality").
_STANDARDS: tuple[tuple[str, str], ...] = (
    ("prose_clarity_rules", "prose_clarity_rules_path"),
    ("prose_contract", "prose_contract_path"),
    ("voice_guide", "voice_guide_path"),
)

_SYSTEM = (
    "You audit finished prose against the author's own written rules, and you cite those rules by "
    "their identifier. You are not a general writing coach: you have no opinions of your own about "
    "style, and a sentence that breaks no rule below is correct however you would have written it. "
    "Report only what a named rule condemns.\n\n"
    "Three refusals that matter more than coverage:\n"
    "1. NEVER quote a passage that is not in the prose verbatim. Copy the exact characters.\n"
    "2. NEVER report the same span twice under different rules — pick the rule that fits best.\n"
    "3. NEVER rewrite the author's voice. A replacement fixes the cited fault and changes nothing "
    "else; if you cannot fix it without rewriting the sentence's character, give no replacement and "
    "say what is wrong instead."
)

_RESPONSE_CONTRACT = (
    "\nReturn ONLY a JSON array, no prose and no code fences. Each item:\n"
    '{"rule": str, "rule_source": "prose_clarity_rules"|"prose_contract"|"voice_guide"'
    '|"forbidden_drift", "severity": "block"|"warn"|"info", "quote": str, '
    '"new_text": str, "why": str}\n\n'
    '`rule` is the identifier as the document writes it — "R2", "The One Test", "drift #18 '
    'Overworked Voice", "contract 7". `quote` is the exact failing text, copied character for '
    "character from the passage, and long enough to be unambiguous but no longer. `new_text` is the "
    'corrected text that replaces the quote — "" for a pure deletion, and omit the key entirely when '
    "you are diagnosing without proposing. `why` is ONE sentence naming the fault, not a lecture.\n\n"
    "Severity: `block` only for a failure of The One Test (a load-bearing beat that cannot be "
    "reconstructed from the prose). `warn` for a clear rule break. `info` for a borderline call.\n\n"
    "Empty array [] if the passage breaks no rule. That is a real and common answer — say it rather "
    "than manufacture a finding."
)


@dataclass(frozen=True)
class AuditFinding:
    """One rule-cited suggestion. Shaped to become a `Suggestion` row without translation."""

    rule: str
    rule_source: str
    severity: Severity
    quote: str
    new_text: str | None
    why: str


@dataclass(frozen=True)
class StyleAuditResult:
    findings: list[AuditFinding] = field(default_factory=list)
    # Which standards actually reached the model. An audit run with `forbidden_drift` missing is a
    # weaker audit that looks identical in the output, so the caller is told rather than left to
    # assume — the same reason `EnrichOut` reports `dialogue_rules_loaded`.
    standards_loaded: list[str] = field(default_factory=list)
    standards_missing: list[str] = field(default_factory=list)
    # The characters NAMED in the passage, which is what activates the cast-scoped drift patterns.
    # Not family tags: `scope_forbidden_drift` derives the families (GENRE, RELATIONSHIP, …) from
    # this list plus the passage's physical vocabulary. Named for what it holds, because an empty
    # list is a real and reportable state — prose written entirely in pronouns names nobody, so no
    # cast-scoped rule fires and the author needs to see that rather than assume full coverage.
    drift_scope_characters: list[str] = field(default_factory=list)
    # Findings discarded because their quote is not in the prose. Surfaced, never silently swallowed:
    # a non-zero count here means the model was inventing evidence on this passage.
    fabricated_dropped: int = 0
    model: str = ""
    tokens_used: int = 0


def _severity(value: object) -> Severity:
    """Map the model's severity word onto the house vocabulary.

    Unlike `advisory_severity`, BLOCK is reachable — but only through The One Test, which the author's
    own `prose_clarity_rules.md` calls a hard gate. Nothing downstream consumes this severity (the
    audit writes no table and gates nothing), so BLOCK here is a ranking for the author's eye, not an
    authority claim over the pipeline.
    """
    word = str(value).strip().lower()
    if word == "block":
        return Severity.BLOCK
    if word == "warn":
        return Severity.WARN
    return Severity.INFO


_KNOWN_SOURCES = frozenset({"prose_clarity_rules", "prose_contract", "voice_guide", "forbidden_drift"})


def _finding(item: dict[str, object]) -> AuditFinding | None:
    """One parsed item, or None if it is structurally unusable. The evidence check is deliberately NOT
    here — it needs the prose, and keeping it at the call site is what lets a dropped fabrication be
    counted rather than silently folded in with items that were merely malformed."""
    quote = str(item.get("quote", "")).strip()
    why = str(item.get("why", "")).strip()
    if not quote or not why:
        return None

    # `new_text` absent => diagnosis only. `new_text` present but equal to the quote => the model
    # proposed a no-op, which renders as a change that changes nothing; drop the replacement and keep
    # the diagnosis, since the fault it names may still be real.
    raw_new = item.get("new_text")
    new_text = str(raw_new) if isinstance(raw_new, str) else None
    if new_text is not None and new_text.strip() == quote.strip():
        new_text = None

    rule = str(item.get("rule", "")).strip() or "unattributed"
    source = str(item.get("rule_source", "")).strip().lower()
    return AuditFinding(
        rule=rule,
        rule_source=source if source in _KNOWN_SOURCES else "unattributed",
        severity=_severity(item.get("severity")),
        quote=quote,
        new_text=new_text,
        why=why,
    )


async def audit_prose(
    session: AsyncSession,
    prose: str,
    *,
    pov: str = "",
    budget: TokenBudget | None = None,
) -> StyleAuditResult:
    """Audit `prose` against the style documents. One model call; writes nothing."""
    if not prose.strip():
        return StyleAuditResult(model=settings.style_audit_model)

    loaded: list[str] = []
    missing: list[str] = []
    sections: list[str] = []
    for name, attr in _STANDARDS:
        content = await load_style_document(session, getattr(settings, attr))
        if content:
            loaded.append(name)
            sections.append(f"=== {name} ===\n{content}")
        else:
            missing.append(name)

    # The drift patterns, scoped by the passage's OWN evidence. `signals=prose` lets the physical
    # vocabulary in the text decide whether CHOREOGRAPHY patterns load, and the roster is read off the
    # page — see `cast_present_in`. This is the first caller of AUDIT mode, which the module was built
    # for and nothing had used: it carries each pattern's warning signs and correction in full, where
    # DRAFT mode carries only names and corrections.
    drift_scope_characters: list[str] = []
    drift_raw = await load_style_document(session, settings.forbidden_drift_path)
    if drift_raw:
        present = cast_present_in(prose)
        scoped = scope_forbidden_drift(drift_raw, pov=pov, present=present, signals=prose, mode=AUDIT)
        if scoped:
            loaded.append("forbidden_drift")
            sections.append(f"=== forbidden_drift ===\n{scoped}")
            drift_scope_characters = sorted(present)
        else:
            missing.append("forbidden_drift")
    else:
        missing.append("forbidden_drift")

    if not sections:
        # Nothing to judge against. Returning an empty finding list here would be indistinguishable
        # from clean prose, so the caller gets the empty `standards_loaded` to read instead.
        return StyleAuditResult(standards_missing=missing, model=settings.style_audit_model)

    pov_line = f"POV character: {pov}\n\n" if pov.strip() else ""
    user = (
        "THE RULES YOU ARE JUDGING AGAINST:\n\n"
        + "\n\n".join(sections)
        + "\n\n=== PASSAGE TO AUDIT ===\n"
        + pov_line
        + prose
        + "\n"
        + _RESPONSE_CONTRACT
    )

    budget = budget or TokenBudget(max_tokens=settings.style_audit_token_budget)
    raw, _usage = await complete_with_rate_limit_fallback(
        setting_key="style_audit_model",
        model=settings.style_audit_model,
        system=_SYSTEM,
        user=user,
        max_tokens=_AUDIT_MAX_TOKENS,
        budget=budget,
        temperature=quality_temperature("style_audit_model"),
        effort=quality_effort("style_audit_model"),
    )

    findings: list[AuditFinding] = []
    dropped = 0
    seen: set[str] = set()
    for item in parse_json_objects(raw):
        finding = _finding(item)
        if finding is None:
            continue
        # Deterministic fabrication guard — the finding's evidence must exist in the author's text.
        if not quote_is_supported(finding.quote, prose):
            dropped += 1
            continue
        # One suggestion per span. Two rules condemning the same words would render as overlapping
        # markers, and `tokenize` (prose.ts:559) drops the overlap silently — so the second finding
        # would vanish from the page while still being counted here.
        key = finding.quote.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        findings.append(finding)

    return StyleAuditResult(
        findings=findings,
        standards_loaded=loaded,
        standards_missing=missing,
        drift_scope_characters=drift_scope_characters,
        fabricated_dropped=dropped,
        model=settings.style_audit_model,
        tokens_used=budget.used,
    )
