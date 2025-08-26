"""Marketing agent template for diffusion experiments.

Agents update their sentiment toward a product when receiving
messages and decide whether to forward the information to friends
based on relationship strength and LLM guidance.
"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

import json_repair
import numpy as np

from agentsociety.agent import CitizenAgentBase, MemoryAttribute
from agentsociety.agent.agent_base import AgentToolbox
from agentsociety.cityagent.blocks.utils import clean_json_response
from agentsociety.memory import Memory
from agentsociety.message import Message

# relationship weight by type
RELATION_WEIGHTS = {
    "spouse": 1.0,
    "sibling": 0.9,
    "cousin": 0.8,
    "friend": 0.6,
    "coworker": 0.5,
    "neighbor": 0.4,
}

RNG = np.random.default_rng(42)

# profile mapping populated by workflow setup function
ID_TO_PROFILE: Dict[int, dict] = {}


async def _consult_llm(
    agent: "MarketingAgent",
    profile: dict,
    sentiment: float,
    adopted: bool,
    message: str,
    friend_names: List[str],
) -> Tuple[float, bool, str, bool, List[str], str, str, str, str]:
    """Query the LLM for updated sentiment, adoption and sharing decision."""
    dialog = [
        {
            "role": "system",
            "content": (
                "You simulate how a consumer reacts to news about the Z-Energy Zero drink. "
                "Reply in JSON {\"sentiment\": float, \"adopted\": bool, \"say\": string, \"share\": bool, "
                "\"suggested_targets\": [string], \"emotion\": string, \"thought\": string, \"attitude\": string, \"current_need\": string}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Background: {profile.get('bio', '')}\n"
                f"Occupation: {profile.get('occupation', '')}\n"
                f"Friends: {', '.join(friend_names) if friend_names else 'none'}\n"
                f"Current sentiment (-1 to 1): {sentiment:.2f}\n"
                f"Has adopted?: {adopted}\n"
                f"Incoming message: {message}\n\nRespond with JSON."
            ),
        },
    ]
    try:
        reply = await agent.llm.atext_request(dialog)
        cleaned = clean_json_response(reply)
        data = json_repair.loads(cleaned)

        raw_sentiment = data.get("sentiment", sentiment)
        new_sentiment = float(raw_sentiment) if isinstance(raw_sentiment, (int, float)) else sentiment

        raw_adopted = data.get("adopted", adopted)
        new_adopted = bool(raw_adopted) if isinstance(raw_adopted, (bool, int)) else adopted

        say = data.get("say", "")
        say = str(say) if isinstance(say, (str, int, float)) else ""

        raw_share = data.get("share", True)
        share = bool(raw_share) if isinstance(raw_share, (bool, int)) else True

        targets = data.get("suggested_targets", [])
        stack = list(targets) if isinstance(targets, list) else []
        suggested: List[str] = []
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                suggested.append(item)
            elif isinstance(item, list):
                stack.extend(item)

        emotion = data.get("emotion", "Neutral")
        emotion = str(emotion) if isinstance(emotion, (str, int, float)) else "Neutral"

        thought = data.get("thought", "")
        thought = str(thought) if isinstance(thought, (str, int, float)) else ""

        attitude = data.get("attitude", "neutral")
        attitude = str(attitude) if isinstance(attitude, (str, int, float)) else "neutral"

        need = data.get("current_need", "none")
        need = str(need) if isinstance(need, (str, int, float)) else "none"
    except Exception as e:  # pragma: no cover - LLM failures
        agent.logger.warning(f"LLM parse failed for {agent.name}: {e}")
        new_sentiment = sentiment
        new_adopted = adopted
        say = message
        share = True
        suggested = []
        emotion = "Neutral"
        thought = ""
        attitude = "neutral"
        need = "none"

    new_sentiment = float(np.clip(new_sentiment, -1, 1))
    return new_sentiment, new_adopted, say, share, suggested, emotion, thought, attitude, need


class MarketingAgent(CitizenAgentBase):
    """Citizen reacting to marketing information."""

    StatusAttributes = [
        MemoryAttribute(name="friends", type=list, default_or_value=[], description="friend ids"),
        MemoryAttribute(name="profile", type=dict, default_or_value={}, description="profile info"),
        MemoryAttribute(name="sentiment", type=float, default_or_value=0.0, description="sentiment [-1,1]"),
        MemoryAttribute(name="emotion", type=str, default_or_value="Neutral", description="current emotion"),
        MemoryAttribute(name="thought", type=str, default_or_value="", description="agent reflection", whether_embedding=True),
        MemoryAttribute(name="adopted", type=bool, default_or_value=False, description="has adopted product"),
        MemoryAttribute(name="attitude", type=str, default_or_value="neutral", description="attitude toward product"),
        MemoryAttribute(name="current_need", type=str, default_or_value="none", description="current need"),
    ]

    def __init__(self, id: int, name: str, toolbox: AgentToolbox, memory: Memory,
                 agent_params=None, blocks=None) -> None:
        super().__init__(id, name, toolbox, memory, agent_params, blocks)
        self.processed_msgs: set[Tuple[int, str]] = set()
        self.max_forwards = (
            agent_params.max_forwards if agent_params and hasattr(agent_params, "max_forwards") else 5
        )

    async def _handle_message(self, content: str, sender_id: int | None = None) -> str:
        profile = await self.memory.status.get("profile") or {}
        sentiment = await self.memory.status.get("sentiment")
        adopted = await self.memory.status.get("adopted")
        friends = await self.memory.status.get("friends") or []
        friend_names = [ID_TO_PROFILE.get(f, {}).get("name", "") for f in friends if f in ID_TO_PROFILE]
        (
            new_sentiment,
            new_adopted,
            say,
            share,
            suggested,
            emotion,
            thought,
            attitude,
            need,
        ) = await _consult_llm(self, profile, sentiment, adopted, content, friend_names)
        await self.memory.status.update("sentiment", new_sentiment)
        await self.memory.status.update("emotion", emotion)
        await self.memory.status.update("thought", thought)
        await self.memory.status.update("adopted", new_adopted)
        await self.memory.status.update("attitude", attitude)
        await self.memory.status.update("current_need", need)
        if share:
            await self._share_message(say, sender_id, suggested)
        return say

    async def _share_message(self, content: str, exclude: int | None, suggested: List[str]) -> None:
        friends = await self.memory.status.get("friends") or []
        if not friends:
            return
        profile = await self.memory.status.get("profile") or {}
        scores: List[Tuple[float, int]] = []
        for fid in friends:
            if exclude is not None and fid == exclude:
                continue
            kind = "friend"
            strength = 0.5
            for conn in profile.get("connections", []):
                if conn["target"] == fid:
                    kind = conn.get("kind", "friend")
                    strength = float(conn.get("strength", 0.5))
                    break
            weight = RELATION_WEIGHTS.get(kind, 0.5) * strength
            if suggested and ID_TO_PROFILE.get(fid, {}).get("name") in suggested:
                weight *= 2.0
            scores.append((weight, fid))
        if not scores:
            return
        sc, neighbors = zip(*scores)
        probs = np.array(sc, dtype=float)
        probs = probs / probs.sum() if probs.sum() else np.ones_like(probs) / len(probs)
        k = min(self.max_forwards, len(neighbors))
        chosen = list(RNG.choice(neighbors, size=k, replace=False, p=probs))
        serialized = json.dumps({"content": content}, ensure_ascii=False)
        for fid in chosen:
            await self.send_message_to_agent(fid, serialized)

    async def do_chat(self, message: Message) -> str:
        sender_id = message.payload.get("from")
        raw = message.payload.get("content", "")
        if not raw:
            return ""
        key = (sender_id or -1, raw)
        if key in self.processed_msgs:
            return ""
        self.processed_msgs.add(key)
        try:
            data = json_repair.loads(raw)
            content = data.get("content", raw)
        except Exception:
            content = raw
        content = str(content)
        if not content:
            return ""
        return await self._handle_message(content, sender_id)

    async def react_to_intervention(self, intervention_message: str):
        await self._handle_message(intervention_message, None)