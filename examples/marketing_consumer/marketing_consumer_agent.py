"""Marketing diffusion example using AgentSociety message hooks.

This script simulates the launch of the **Z-Energy Zero** drink. Consumers
first receive an advertisement (8:00), later read a headache rumour (10:00),
and finally see an official rebuttal (12:00). Each agent's sentiment and
adoption decision are determined by an LLM that takes the agent's background
into account.  Agents then forward their own messages to friends using
``send_message_to_agent`` similar to ``examples/polarization/message_agent.py``.

Two implementation details correspond to earlier improvement suggestions:

* **Agent messaging** – A :class:`MarketingAgent` subclass of
  :class:`CitizenAgentBase` implements ``forward`` and ``do_chat`` to exchange
  messages through the simulator rather than iterating over a NetworkX graph.

* **Explicit profile mapping** – agent IDs from the simulator are matched to
  ``agents.json`` via the ``id`` field so the code does not rely on list
  ordering.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

import json_repair
import numpy as np

from agentsociety.agent import CitizenAgentBase, MemoryAttribute
from agentsociety.agent.agent_base import AgentToolbox
from agentsociety.cityagent.blocks.utils import clean_json_response
from agentsociety.logger import get_logger
from agentsociety.memory import Memory
from agentsociety.message import Message

from agentsociety.cityagent import default
from agentsociety.configs import AgentsConfig, Config, EnvConfig, ExpConfig, LLMConfig, MapConfig
from agentsociety.configs.agent import AgentConfig
from agentsociety.configs.exp import WorkflowStepConfig, WorkflowType
from agentsociety.environment import EnvironmentConfig
from agentsociety.llm import LLMProviderType
from agentsociety.simulation import AgentSociety
from agentsociety.storage import DatabaseConfig


# ---------------------------------------------------------------------------
# Profiles and relationship metadata
# ---------------------------------------------------------------------------

PROFILE_PATH = Path(__file__).with_name("agents.json")
PROFILES: List[dict] = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
ID_TO_PROFILE: Dict[int, dict] = {p["id"]: p for p in PROFILES}
NUM_AGENTS = len(PROFILES)

# Relationship importance used to weight forwarding probability
RELATION_WEIGHTS = {
    "spouse": 1.0,
    "sibling": 0.9,
    "cousin": 0.8,
    "friend": 0.6,
    "coworker": 0.5,
    "neighbor": 0.4,
}

RNG = np.random.default_rng(42)


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

async def consult_llm(
    agent: "MarketingAgent",
    profile: dict,
    current_sentiment: float,
    adopted: bool,
    message: str,
    friend_names: List[str],
) -> Tuple[float, bool, str, bool, List[str]]:
    """Ask LLM for updated sentiment, adoption, and sharing decision."""

    dialog = [
        {
            "role": "system",
            "content": (
                "You simulate how a consumer reacts to news about the Z-Energy Zero "
                "drink. Reply in JSON {\"sentiment\": float, \"adopted\": bool, "
                "\"say\": string, \"share\": bool, \"suggested_targets\": [string]}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Background: {profile['bio']}\n"
                f"Occupation: {profile['occupation']}\n"
                f"Friends: {', '.join(friend_names) if friend_names else 'none'}\n"
                f"Current sentiment (-1 to 1): {current_sentiment:.2f}\n"
                f"Has adopted Z-Energy Zero?: {adopted}\n"
                f"Incoming message: {message}\n\nRespond with JSON."
            ),
        },
    ]

    try:
        reply = await agent.llm.atext_request(dialog)
        cleaned = clean_json_response(reply)
        data = json_repair.loads(cleaned)
        new_sentiment = float(data.get("sentiment", current_sentiment))
        new_adopted = bool(data.get("adopted", adopted))
        say = str(data.get("say", ""))
        share = bool(data.get("share", True))
        targets = data.get("suggested_targets", [])
        if not isinstance(targets, list):
            targets = []

        suggested: List[str] = []

        def _collect(items):
            for item in items:
                if isinstance(item, str):
                    suggested.append(item)
                elif isinstance(item, list):
                    _collect(item)

        _collect(targets)
    except Exception as e:
        get_logger().warning(
            f"LLM parse failed for {agent.name}: {e}; reply={locals().get('reply', '')}"
        )
        new_sentiment = current_sentiment
        new_adopted = adopted
        say = message
        share = True
        suggested = []

    new_sentiment = float(np.clip(new_sentiment, -1, 1))
    return new_sentiment, new_adopted, say, share, suggested


# ---------------------------------------------------------------------------
# Marketing agent class
# ---------------------------------------------------------------------------


class MarketingAgent(CitizenAgentBase):
    """Citizen that reacts to marketing messages and chats with friends."""

    StatusAttributes = [
        MemoryAttribute(
            name="friends",
            type=list,
            default_or_value=[],
            description="agent's friends list",
        ),
        MemoryAttribute(
            name="profile",
            type=dict,
            default_or_value={},
            description="agent's profile information",
        ),
        MemoryAttribute(
            name="sentiment",
            type=float,
            default_or_value=0.0,
            description="sentiment towards Z-Energy Zero (-1 to 1)",
        ),
        MemoryAttribute(
            name="adopted",
            type=bool,
            default_or_value=False,
            description="whether the agent has adopted the drink",
        ),
    ]

    def __init__(
        self,
        id: int,
        name: str,
        toolbox: AgentToolbox,
        memory: Memory,
        agent_params=None,
        blocks=None,
    ) -> None:
        super().__init__(id, name, toolbox, memory, agent_params, blocks)
        self.processed_events: set[int] = set()
        # Track processed (sender, message) pairs to avoid repeated LLM calls
        self.processed_msgs: set[tuple[int, str]] = set()
        self.max_forwards = (
            agent_params.max_forwards
            if agent_params is not None and hasattr(agent_params, "max_forwards")
            else 5
        )

    async def _process_message(self, message: str) -> Tuple[str, bool, List[str]]:
        profile = await self.memory.status.get("profile")
        sentiment = await self.memory.status.get("sentiment")
        adopted = await self.memory.status.get("adopted")
        friends = await self.memory.status.get("friends")
        friend_names = [ID_TO_PROFILE[f]["name"] for f in friends]
        new_sentiment, new_adopted, say, share, suggested = await consult_llm(
            self, profile, sentiment, adopted, message, friend_names
        )
        await self.memory.status.update("sentiment", new_sentiment)
        await self.memory.status.update("adopted", new_adopted)
        return say, share, suggested

    async def forward(self) -> None:
        await self.update_motion()
        day, seconds = self.environment.get_datetime()
        hour = int(seconds // 3600)

        events = {
            8: {
                "message": (
                    "Advertisement 08:00 - Z-Energy Zero hits the shelves: a zero-sugar "
                    "energy drink with natural caffeine and B vitamins for steady energy "
                    "without the crash."
                ),
                "reach_prob": 0.1,
                "target_tags": ["fitness", "health"],
            },
            10: {
                "message": (
                    "Rumor 10:00 - Social media posts claim Z-Energy Zero gives people "
                    "headaches and jitters."
                ),
                "reach_prob": 0.7,
            },
            12: {
                "message": (
                    "Rebuttal 12:00 - The brand releases official lab results showing "
                    "ingredients are safe and no evidence links the drink to headaches."
                ),
                "reach_prob": 0.9,
            },
        }

        if hour in events and hour not in self.processed_events:
            profile = await self.memory.status.get("profile")
            event = events[hour]
            interests = set(profile.get("interests", []))
            tags = set(event.get("target_tags", []))
            prob = event.get("reach_prob", 1.0)
            if RNG.random() <= prob and (not tags or interests & tags):
                self.processed_events.add(hour)
                say, share, suggested = await self._process_message(event["message"])
                if share:
                    await self._share_message(say, depth=1, suggested=suggested)

    async def _share_message(
        self,
        content: str,
        depth: int,
        exclude: int | None = None,
        suggested: List[str] | None = None,
    ):
        friends = await self.memory.status.get("friends")
        if not friends:
            return
        profile = await self.memory.status.get("profile")
        interests_self = set(profile.get("interests", []))
        data = ID_TO_PROFILE
        day, seconds = self.environment.get_datetime()
        hour = int(seconds // 3600)
        scores: List[Tuple[float, int]] = []
        for fid in friends:
            if exclude is not None and fid == exclude:
                continue
            conn_list = profile.get("connections", [])
            kind = "friend"
            strength = 0.5
            for conn in conn_list:
                if conn["target"] == fid:
                    kind = conn.get("kind", "friend")
                    strength = float(conn.get("strength", 0.5))
                    break
            rel_weight = RELATION_WEIGHTS.get(kind, 0.5)
            friend_profile = data.get(fid, {})
            interests_friend = set(friend_profile.get("interests", []))
            union = interests_self | interests_friend
            similarity = (len(interests_self & interests_friend) / len(union)) if union else 0.0
            score = strength * rel_weight * (1 + similarity)
            if suggested and friend_profile.get("name") in suggested:
                score *= 2.0
            scores.append((score, fid))
        if not scores:
            return
        sc, neighbors = zip(*scores)
        probs = np.array(sc, dtype=float)
        probs = probs / probs.sum() if probs.sum() else np.ones_like(probs) / len(probs)
        k = min(self.max_forwards, len(neighbors))
        chosen = list(RNG.choice(neighbors, size=k, replace=False, p=probs))

        serialized = json.dumps({"content": content, "depth": depth}, ensure_ascii=False)
        for fid in chosen:
            await self.send_message_to_agent(fid, serialized)
            CHAT_LOG.append((hour, profile["name"], data[fid]["name"], content))

    async def do_chat(self, message: Message) -> str:
        payload = message.payload
        sender_id = payload.get("from")
        raw = payload.get("content", "")
        try:
            data = json_repair.loads(raw)
            content = data.get("content", raw)
            depth = int(data.get("depth", 1))
        except Exception:
            content = raw
            depth = 1
        if not content or depth > 3 or sender_id is None:
            return ""
        key = (sender_id, content)
        if key in self.processed_msgs:
            return ""
        say, share, suggested = await self._process_message(content)
        self.processed_msgs.add(key)
        if share:
            await self._share_message(say, depth + 1, exclude=sender_id, suggested=suggested)
        return say


# ---------------------------------------------------------------------------
# Workflow helper functions
# ---------------------------------------------------------------------------


async def setup_agents(simulation: AgentSociety) -> None:
    """Map profiles to agents and populate friend lists."""

    citizen_ids = await simulation.filter()
    missing = set(ID_TO_PROFILE) - set(citizen_ids)
    if missing:
        raise ValueError(f"Profiles provided for unknown ids: {missing}")

    for cid in citizen_ids:
        profile = ID_TO_PROFILE[cid]
        friends = [int(conn["target"]) for conn in profile.get("connections", [])]
        await simulation.update([cid], "friends", friends)
        await simulation.update([cid], "profile", profile)


CHAT_LOG: List[Tuple[int, str, str, str]] = []


async def print_results(simulation: AgentSociety) -> None:
    """Display final sentiment and adoption along with chat log."""

    citizen_ids = await simulation.filter()
    sentiments = await simulation.gather("sentiment", citizen_ids, keep_id=True)
    adopted = await simulation.gather("adopted", citizen_ids, keep_id=True)
    adopted_flags: List[bool] = []
    for cid in sorted(citizen_ids):
        sentiment = float(sentiments.get(cid, 0.0))
        adopted_flag = bool(adopted.get(cid, False))
        adopted_flags.append(adopted_flag)
        name = ID_TO_PROFILE[cid]["name"]
        print(f"{name}: sentiment={sentiment:.2f}, adopted={adopted_flag}")

    if adopted_flags:
        rate = sum(adopted_flags) / len(adopted_flags)
        print(f"\nOverall adoption rate: {rate:.2%}")

    print("\nChat log (hour, sender -> receiver: message)")
    for hour, sender, receiver, msg in CHAT_LOG:
        print(f"{hour:02d}, {sender} -> {receiver}: {msg}")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


config = Config(
    llm=[
        LLMConfig(
            provider="openai",
            base_url=None,
            api_key="",
            model="gpt-4o-mini",
            concurrency=200,
            timeout=60,
        )
    ],
    env=EnvConfig(
        db=DatabaseConfig(enabled=True, db_type="sqlite"),
        home_dir="/home/txdazure/githubs/agentsociety_data",
    ),
    # No city map required for this information diffusion example.
    map=MapConfig(file_path="/home/txdazure/githubs/agentsociety_data/beijing.pb"),
    agents=AgentsConfig(
        citizens=[
            AgentConfig(
                agent_class=MarketingAgent,          # 传入类对象而非字符串
                number=NUM_AGENTS,
                agent_params={"max_forwards": 5},
            )
        ]
    ),
    exp=ExpConfig(
        name="marketing_campaign",
        workflow=[
            WorkflowStepConfig(type=WorkflowType.FUNCTION, func=setup_agents),
            WorkflowStepConfig(type=WorkflowType.STEP, steps=13, ticks_per_step=3600),
            WorkflowStepConfig(type=WorkflowType.FUNCTION, func=print_results),
        ],
        environment=EnvironmentConfig(start_tick=0),
    ),
)
config = default(config)


async def main() -> None:
    agentsociety = AgentSociety.create(config)
    try:
        await agentsociety.init()
        await agentsociety.run()
    finally:
        await agentsociety.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
