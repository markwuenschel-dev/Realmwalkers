"""Orchestrate scene context assembly (DESIGN §4, §7)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import Job
from dominion.workers.budget import TokenBudget
from dominion.workers.context.contracts import load_scene_packet_fields
from dominion.workers.context.dialogue_rules import load_dialogue_rules
from dominion.workers.context.draft_memory import build_draft_memory
from dominion.workers.context.forbidden_drift import load_forbidden_drift
from dominion.workers.context.resolve import resolve_job
from dominion.workers.context.revision import load_revision_state
from dominion.workers.context.types import RevisionState, SceneContext, ScenePacketRequiredError
from dominion.workers.pov import effective_pov


async def assemble_context(session: AsyncSession, job: Job) -> SceneContext:
    """Load everything a draft (or revision) needs. A job that names a ScenePacket must reference an
    approved, non-stale one — otherwise drafting fails closed (no silent chapter-packet fallback)."""
    resolved = await resolve_job(session, job)
    if resolved.scene_packet_id is None:
        raise ScenePacketRequiredError(
            f"no scene packet for ch{resolved.chapter.chapter_no} sc{resolved.scene_no} — derive and "
            "approve a scene packet before drafting"
        )

    memory = await build_draft_memory(session, resolved, job)
    packet = await load_scene_packet_fields(session, resolved.scene_packet_id)
    revision = await load_revision_state(session, job) if job.target_scene_id is not None else RevisionState(None, None)

    beat = resolved.beat
    # Effective POV = the beat's per-scene override, else the chapter POV. resolved.profile is already
    # the effective POV's profile (resolve_job loads it that way), so the scene drafts in that voice.
    pov = effective_pov(beat, resolved.chapter)
    return SceneContext(
        book_id=resolved.book_id,
        chapter_id=resolved.chapter.id,
        pov=pov,
        scene_no=resolved.scene_no,
        tags=list(beat.tags or []),
        characters_present=list(beat.characters_present or []),
        beat_text=beat.beat_text,
        expected_state_changes=beat.expected_state_changes,
        knowledge_injections=list(beat.knowledge_injections or []),
        voice_spec=resolved.profile.voice_spec if resolved.profile else None,
        budget=TokenBudget(max_tokens=job.token_budget),
        target_words=beat.target_words,
        dialogue_rules=load_dialogue_rules([pov, *(beat.characters_present or [])]),
        # Scoped by family: the physical signal comes from the beat's own tags and text, so a scene
        # that never moves a body does not carry the choreography patterns into its prompt.
        forbidden_drift=load_forbidden_drift(
            pov=pov,
            present=[pov, *(beat.characters_present or [])],
            signals=" ".join([*(beat.tags or []), beat.beat_text or ""]),
        ),
        exemplars=memory.exemplars,
        canon=memory.canon,
        pov_summary=memory.pov_summary,
        ledger=memory.ledger,
        prior_scene_tail=memory.prior_scene_tail,
        scene_packet_id=packet.scene_packet_id,
        chapter_contract=packet.chapter_contract,
        scene_contract=packet.scene_contract,
        word_budget=packet.word_budget,
        reader_state_contract=packet.reader_state_contract,
        reviewer_contract=packet.reviewer_contract,
        contract=packet.contract,
        fidelity=packet.fidelity,
        prior_prose=revision.prior_prose,
        revise_feedback=revision.revise_feedback,
        target_pass=job.target_pass,
        # Timeline memory from active production DraftRunTimeline (if any)
        # The drafter context can use these to avoid repeating prior exit state facts etc.
        prior_exit_state=getattr(memory, "prior_exit_state", None),
        spent_beats=list(getattr(memory, "spent_beats", []) or []),
        reader_learned=list(getattr(memory, "reader_learned", []) or []),
        must_not_repeat=list(getattr(memory, "must_not_repeat", []) or []),
        chapter_so_far_summary=getattr(memory, "chapter_so_far_summary", None),
    )
