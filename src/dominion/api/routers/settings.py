"""Runtime model selection per agent role (Haiku / Sonnet / Opus).

Every agent already reads its model from `settings.<role>_model`. This router persists a per-role
override (model_overrides table) and mutates the live `settings` singleton so the change takes effect
on the very next agent call — the worker drain runs in this same process — with no redeploy.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.models import ModelOverride
from dominion.shared.schemas import ModelSettingOut, ModelSettingsOut, ModelSettingUpdateIn

log = structlog.get_logger()
router = APIRouter(prefix="/settings", tags=["settings"])

# Each customizable agent role -> the `settings` attribute it reads + a label/description for the UI.
# (The Oracle is deterministic — it has no model and is intentionally absent.)
ROLES: list[tuple[str, str, str]] = [
    ("draft_model", "Drafter & planner", "Writes scene prose and proposes the gate-1 beats"),
    ("review_model", "Reviewers & summaries", "Continuity / combat / pacing / voice reviewers + rolling summaries"),
    ("enrich_model", "Enrichment specialists", "Combat / sensory / dialogue enrichment passes"),
    ("packet_author_model", "Packet author", "Authors the chapter knowledge packet from canon + outline"),
    ("packet_qa_model", "Packet QA", "Validates the proposed packet before approval"),
    ("scene_packet_author_model", "ScenePacket author",
     "Localizes the chapter packet into each scene's reader/POV/reveal contract (once per scene)"),
    ("scene_packet_qa_model", "ScenePacket QA",
     "Attacks each scene packet before approval (once per scene)"),
]
_ROLE_KEYS = {r[0] for r in ROLES}

# The three tiers offered in the UI -> the model id stored + used.
TIERS: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
}


def tier_of(model_id: str | None) -> str | None:
    """Which tier a configured model id belongs to (by family), so the UI can preselect it even when
    the stored id is a dated alias (e.g. claude-haiku-4-5-20251001 -> haiku)."""
    for tier in ("opus", "sonnet", "haiku"):
        if tier in (model_id or ""):
            return tier
    return None


async def apply_model_overrides(session: AsyncSession) -> int:
    """Load persisted overrides into the live settings singleton. Called once on app startup so a model
    choice survives a redeploy. Unknown/removed roles are ignored."""
    rows = (await session.execute(select(ModelOverride))).scalars().all()
    applied = 0
    for row in rows:
        if row.setting_name in _ROLE_KEYS:
            setattr(settings, row.setting_name, row.model)
            applied += 1
    return applied


def _meta(setting: str) -> tuple[str, str]:
    return next((label, desc) for key, label, desc in ROLES if key == setting)


@router.get("/models", response_model=ModelSettingsOut)
async def get_models(session: SessionDep) -> ModelSettingsOut:
    """Every customizable agent's current model + which tier it is, plus the tier -> id map."""
    agents = [
        ModelSettingOut(
            setting=key, label=label, description=desc,
            model=getattr(settings, key), tier=tier_of(getattr(settings, key)),
        )
        for key, label, desc in ROLES
    ]
    return ModelSettingsOut(agents=agents, tiers=TIERS)


@router.put("/models", response_model=ModelSettingOut)
async def set_model(body: ModelSettingUpdateIn, session: SessionDep) -> ModelSettingOut:
    """Point one agent role at Haiku / Sonnet / Opus. Applies live + persists."""
    if body.setting not in _ROLE_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown agent setting '{body.setting}'")
    model = TIERS.get(body.tier)
    if model is None:
        raise HTTPException(status_code=422, detail="tier must be haiku, sonnet, or opus")
    setattr(settings, body.setting, model)  # live: the next agent call reads this
    existing = await session.get(ModelOverride, body.setting)
    if existing is None:
        session.add(ModelOverride(setting_name=body.setting, model=model))
    else:
        existing.model = model
    await session.commit()
    label, desc = _meta(body.setting)
    log.info("settings.model_changed", setting=body.setting, model=model)
    return ModelSettingOut(setting=body.setting, label=label, description=desc, model=model, tier=body.tier)
