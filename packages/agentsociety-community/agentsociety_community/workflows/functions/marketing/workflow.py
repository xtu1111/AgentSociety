"""Workflow for marketing diffusion using configurable message steps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from agentsociety.configs.exp import ExpConfig, WorkflowStepConfig, WorkflowType
from agentsociety.environment import EnvironmentConfig
from agentsociety.simulation import AgentSociety
from agentsociety.cityagent import bind_agent_info

from ....agents.citizens.marketing import marketing_agent


async def setup_agents(simulation: AgentSociety) -> None:
    """Load agent profiles and populate friend lists."""

    profiles: List[dict] = []
    for cfg in simulation.config.agents.citizens:
        if cfg.memory_from_file:
            try:
                data = json.loads(Path(cfg.memory_from_file).read_text(encoding="utf-8"))
                if isinstance(data, list):
                    profiles.extend(data)
            except Exception:
                continue

    id_to_profile = {int(p["id"]): p for p in profiles if "id" in p}
    citizen_ids = await simulation.filter()
    for cid in citizen_ids:
        profile = id_to_profile.get(cid, {})
        friends = [int(conn.get("target")) for conn in profile.get("connections", [])]
        await simulation.update([cid], "friends", friends)
        await simulation.update([cid], "profile", profile)
        marketing_agent.ID_TO_PROFILE[cid] = profile


async def report_sentiment(simulation: AgentSociety):
    """Print each agent's final sentiment, emotion and adoption rate."""
    ids = await simulation.filter()
    sentiments = await simulation.gather("sentiment", ids, flatten=True, keep_id=True)
    emotions = await simulation.gather("emotion", ids, flatten=True, keep_id=True)
    adopted = await simulation.gather("adopted", ids, flatten=True, keep_id=True)
    exposures = await simulation.gather("exposure_count", ids, flatten=True, keep_id=True)
    shares = await simulation.gather("messages_shared", ids, flatten=True, keep_id=True)
    for cid in sorted(ids):
        name = marketing_agent.ID_TO_PROFILE.get(cid, {}).get("name", str(cid))
        val = sentiments.get(cid, 0.0)
        emo = emotions.get(cid, "Neutral")
        adopt_flag = bool(adopted.get(cid, False))
        exp = exposures.get(cid, 0)
        sh = shares.get(cid, 0)
        print(f"{name}: sentiment={val:.2f}, emotion={emo}, adopted={adopt_flag}, exposures={exp}, shares={sh}")
    if adopted:
        rate = sum(1 for v in adopted.values() if v) / len(adopted)
        total_exp = sum(exposures.values())
        total_sh = sum(shares.values())
        print(f"\nAdoption rate: {rate:.2%}, total exposures: {total_exp}, total shares: {total_sh}")


MARKETING_WORKFLOW = ExpConfig(
    name="marketing_campaign",
    workflow=[
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=bind_agent_info),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=setup_agents),
        WorkflowStepConfig(
            type=WorkflowType.MARKETING_MESSAGE,
            intervene_message=(
                "Advertisement 08:00 - Z-Energy Zero hits the shelves: a zero-sugar energy drink "
                "with natural caffeine and B vitamins for steady energy without the crash."
            ),
            reach_prob=0.6,
            source="company",
        ),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(
            type=WorkflowType.MARKETING_MESSAGE,
            intervene_message=(
                "Rumor 10:00 - Social media posts claim Z-Energy Zero gives people headaches and jitters."
            ),
            reach_prob=0.8,
            source="influencer",
        ),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(
            type=WorkflowType.MARKETING_MESSAGE,
            intervene_message=(
                "Rebuttal 12:00 - The brand releases official lab results showing ingredients are safe "
                "and no evidence links the drink to headaches."
            ),
            reach_prob=0.7,
            source="company",
        ),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=report_sentiment),
    ],
    environment=EnvironmentConfig(start_tick=8 * 3600),
)


# default initialization functions for this workflow
INIT_FUNCS: List = [bind_agent_info, setup_agents]
