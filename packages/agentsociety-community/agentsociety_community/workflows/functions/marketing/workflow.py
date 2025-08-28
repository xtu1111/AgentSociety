"""Workflow for marketing diffusion using configurable message steps."""

from __future__ import annotations

from agentsociety.configs.exp import ExpConfig, WorkflowStepConfig, WorkflowType
from agentsociety.environment import EnvironmentConfig
from agentsociety.simulation import AgentSociety

from ...agents.citizens.marketing import marketing_agent


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
        WorkflowStepConfig(
            type=WorkflowType.MARKETING_MESSAGE,
            intervene_message=(
                "Advertisement 08:00 - Z-Energy Zero hits the shelves: a zero-sugar energy drink "
                "with natural caffeine and B vitamins for steady energy without the crash."
            ),
            reach_prob=0.6,
        ),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(
            type=WorkflowType.MARKETING_MESSAGE,
            intervene_message=(
                "Rumor 10:00 - Social media posts claim Z-Energy Zero gives people headaches and jitters."
            ),
            reach_prob=0.8,
        ),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(
            type=WorkflowType.MARKETING_MESSAGE,
            intervene_message=(
                "Rebuttal 12:00 - The brand releases official lab results showing ingredients are safe "
                "and no evidence links the drink to headaches."
            ),
            reach_prob=0.7,
        ),
        WorkflowStepConfig(type=WorkflowType.STEP, steps=2, ticks_per_step=3600),
        WorkflowStepConfig(type=WorkflowType.FUNCTION, func=report_sentiment),
    ],
    environment=EnvironmentConfig(start_tick=8 * 3600),
)
