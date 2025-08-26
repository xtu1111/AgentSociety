"""Workflow for marketing diffusion with advertisement, rumor and rebuttal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Iterable

from agentsociety.configs.exp import ExpConfig, WorkflowStepConfig, WorkflowType
import numpy as np
from agentsociety.environment import EnvironmentConfig
from agentsociety.simulation import AgentSociety
from agentsociety.logger import get_logger

from ...agents.citizens.marketing import marketing_agent

PROFILE_PATH = Path(__file__).resolve().parents[4] / "examples" / "marketing_consumer" / "agents.json"
PROFILES: List[dict] = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
ID_TO_PROFILE: Dict[int, dict] = {p["id"]: p for p in PROFILES}
marketing_agent.ID_TO_PROFILE = ID_TO_PROFILE

LOGGER = get_logger(__name__)
RNG = np.random.default_rng(42)


def _select_recipients(profiles: Iterable[dict], reach_prob: float = 1.0, tags: Iterable[str] | None = None) -> List[int]:
    """Return agent ids chosen by probability and optional interest tags."""
    chosen: List[int] = []
    tag_set = set(tags or [])
    for prof in profiles:
        interests = set(prof.get("interests", []))
        if tag_set and not (interests & tag_set):
            continue
        if RNG.random() <= reach_prob:
            chosen.append(prof["id"])
    return chosen


async def setup_agents(simulation: AgentSociety):
    """Populate each agent's friends and profile info."""
    citizen_ids = await simulation.filter()
    for cid in citizen_ids:
        profile = ID_TO_PROFILE[cid]
        friends = [conn["target"] for conn in profile.get("connections", [])]
        await simulation.update([cid], "friends", friends)
        await simulation.update([cid], "profile", profile)


async def send_advertisement(simulation: AgentSociety):
    msg = (
        "Advertisement 08:00 - Z-Energy Zero hits the shelves: a zero-sugar energy drink "
        "with natural caffeine and B vitamins for steady energy without the crash."
    )
    ids = _select_recipients(PROFILES, reach_prob=0.6, tags=["fitness", "health"])
    try:
        await simulation.send_intervention_message(msg, ids)
    except Exception as exc:  # pragma: no cover - broadcast failures
        LOGGER.warning(f"advertisement broadcast failed: {exc}")


async def send_rumor(simulation: AgentSociety):
    msg = (
        "Rumor 10:00 - Social media posts claim Z-Energy Zero gives people headaches and jitters."
    )
    ids = _select_recipients(PROFILES, reach_prob=0.8)
    try:
        await simulation.send_intervention_message(msg, ids)
    except Exception as exc:  # pragma: no cover - broadcast failures
        LOGGER.warning(f"rumor broadcast failed: {exc}")


async def send_rebuttal(simulation: AgentSociety):
    msg = (
        "Rebuttal 12:00 - The brand releases official lab results showing ingredients are safe and no evidence links the drink to headaches."
    )
    ids = _select_recipients(PROFILES, reach_prob=0.7)
    try:
        await simulation.send_intervention_message(msg, ids)
    except Exception as exc:  # pragma: no cover - broadcast failures
        LOGGER.warning(f"rebuttal broadcast failed: {exc}")


async def report_sentiment(simulation: AgentSociety):
    """Print each agent's final sentiment, emotion and adoption rate."""

    ids = await simulation.filter()
    sentiments = await simulation.gather("sentiment", ids, flatten=True, keep_id=True)
    emotions = await simulation.gather("emotion", ids, flatten=True, keep_id=True)
    adopted = await simulation.gather("adopted", ids, flatten=True, keep_id=True)
    for cid in sorted(ids):
        name = ID_TO_PROFILE[cid]["name"]
        val = sentiments.get(cid, 0.0)
        emo = emotions.get(cid, "Neutral")
        adopt_flag = bool(adopted.get(cid, False))
        print(f"{name}: sentiment={val:.2f}, emotion={emo}, adopted={adopt_flag}")
    if adopted:
        rate = sum(1 for v in adopted.values() if v) / len(adopted)
        print(f"\nAdoption rate: {rate:.2%}")


MARKETING_WORKFLOW = ExpConfig(
    name="marketing_campaign",
    workflow=[
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=setup_agents),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=send_advertisement),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=send_rumor),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=send_rebuttal),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=report_sentiment),
    ],
    environment=EnvironmentConfig(start_tick=8 * 3600),
)