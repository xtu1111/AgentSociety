"""Marketing agent template for diffusion experiments.

Agents update their sentiment toward a product when receiving
messages and decide whether to forward the information to friends
based on relationship strength and LLM guidance.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import json_repair
import numpy as np
import re

from pydantic import Field

from agentsociety.agent import AgentParams, CitizenAgentBase, MemoryAttribute
from agentsociety.agent.agent_base import AgentToolbox
from agentsociety.cityagent.blocks.utils import clean_json_response
from agentsociety.memory import Memory
from agentsociety.message import Message
from agentsociety.storage.type import StorageDialog, StorageDialogType

RNG = np.random.default_rng(42)

# profile mapping populated during initialization
ID_TO_PROFILE: Dict[int, dict] = {}

# coefficient for interest similarity boost
BETA = 0.5

# canonical emotion categories and numeric sentiment scores
EMOTION_NORMALIZE_MAP = {
    # English
    "interested": "interested",
    "curious": "curious",
    "relaxed": "relaxed",
    "neutral": "neutral",
    "uninterested": "uninterested",
    "skeptical": "skeptical",
    "dislike": "dislike",
    # Japanese
    "興味津々": "interested",
    "好奇心": "curious",
    "リラックス": "relaxed",
    "中立": "neutral",
    "無関心": "uninterested",
    "懐疑的": "skeptical",
    "嫌い": "dislike",
    # Chinese
    "感兴趣": "interested",
    "好奇": "curious",
    "放松": "relaxed",
    "中立": "neutral",
    "不感兴趣": "uninterested",
    "怀疑": "skeptical",
    "讨厌": "dislike",
}

EMOTION_SCORE_MAP = {
    "dislike": -0.6,
    "skeptical": -0.4,
    "uninterested": -0.2,
    "neutral": 0.0,
    "relaxed": 0.2,
    "curious": 0.4,
    "interested": 0.6,
}


class MarketingAgentConfig(AgentParams):
    """Configuration options for :class:`MarketingAgent`."""

    max_forwards: int = Field(
        default=5, description="Maximum number of friends to forward messages to"
    )
    sentiment_adoption_threshold: float = Field(
        default=0.6,
        description=(
            "Minimum sentiment required before automatically adopting based on "
            "their aggregate sentiment"
        ),
    )

def _extract_text(raw: str) -> str:
    """Best-effort extraction of human-readable text from potential JSON."""
    text = raw.strip()
    if re.match(r"^content\s*[:\uff1a]", text, flags=re.IGNORECASE):
        text = re.sub(r"^content\s*[:\uff1a]\s*", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            return parsed
        if isinstance(parsed, dict):
            if isinstance(parsed.get("content"), str):
                return parsed["content"]
            if isinstance(parsed.get("message"), str):
                return parsed["message"]
        if isinstance(parsed, list) and parsed:
            first = parsed[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict) and isinstance(first.get("content"), str):
                return first["content"]
    except Exception:  # pragma: no cover - heuristic parsing
        match = re.search(
            r""""?content"?[:\uff1a]\s*(?:"([^"]*)"|'([^']*)')""",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1) or match.group(2) or text
    return text


async def _consult_llm(
    agent: "MarketingAgent",
    profile: dict,
    sentiment: float,
    adopted: bool,
    message: str,
    friend_names: List[str],
    exposure: int,
) -> Tuple[float, bool, str, bool, List[str], str, str, str, str]:
    """Query the LLM for updated sentiment, adoption and sharing decision."""
    dialog = [
        {
            "role": "system",
            "content": (
                "You are a person chatting with friends about an incoming piece of marketing or news. "
                "Speak casually in first person and mention if you've heard similar news before. "
                "Speak in Japanese."
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
                f"Seen before?: {'yes' if exposure > 0 else 'no'}\n"
                f"Incoming message: {message}\n\nRespond with JSON."
            ),
        },
    ]
    try:
        reply = await agent.llm.atext_request(dialog)
        cleaned = clean_json_response(reply)
        data = json_repair.loads(cleaned)

        raw_sentiment = data.get("sentiment")
        if isinstance(raw_sentiment, (int, float)):
            new_sentiment = float(raw_sentiment)
        else:
            label = data.get("attitude")
            if label is None or str(label) not in ATTITUDE_SENTIMENT_MAP:
                label = data.get("emotion")
            if isinstance(label, (str, int, float)):
                mapped = ATTITUDE_SENTIMENT_MAP.get(str(label))
                new_sentiment = mapped if mapped is not None else sentiment
            else:
                new_sentiment = sentiment

        raw_adopted = data.get("adopted", adopted)
        new_adopted = bool(raw_adopted) if isinstance(raw_adopted, (bool, int)) else adopted

        say_raw = data.get("say", "")
        say = _extract_text(str(say_raw))

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

        await agent.memory.stream.add(topic="marketing", description=message)
        await agent.save_agent_thought(thought)
    except Exception as e:  # pragma: no cover - LLM failures
        agent.logger.warning(
            f"LLM parse failed for agent {getattr(agent, '_name', agent.id)}: {e}"
        )
        new_sentiment = sentiment
        new_adopted = adopted
        say = message
        share = True
        suggested = []
        emotion = "Neutral"
        thought = f"LLM parse failed: {e}"
        attitude = "neutral"
        need = "none"

        await agent.memory.stream.add(topic="marketing", description=message)
        await agent.save_agent_thought(thought)

    new_sentiment = float(np.clip(new_sentiment, -1, 1))
    return new_sentiment, new_adopted, say, share, suggested, emotion, thought, attitude, need


def _similarity(tags: List[str], profile: dict) -> float:
    """Compute similarity between message tags and a profile's interests/profession."""
    if not tags:
        return 0.0
    tags_set = set(tags)
    interests = set(profile.get("interests", []))
    inter = tags_set & interests
    sim_interests = len(inter) / len(tags_set)
    profession = profile.get("profession", "")
    sim_prof = 1.0 if profession and profession in tags_set else 0.0
    return max(sim_interests, sim_prof)


async def _extract_profile_from_memory(memory: Memory) -> dict:
    """Gather basic profile information stored in agent memory."""
    profile: Dict[str, object] = {}
    for key in [
        "name",
        "age",
        "occupation",
        "profession",
        "interests",
        "connections",
    ]:
        try:
            profile[key] = await memory.status.get(key)
        except KeyError:
            continue
    return profile


class MarketingAgent(CitizenAgentBase):
    """Citizen reacting to marketing information."""

    ParamsType = MarketingAgentConfig
    StatusAttributes = [
        MemoryAttribute(name="friends", type=list, default_or_value=[], description="friend ids"),
        MemoryAttribute(name="connections", type=list, default_or_value=[], description="social connections"),
        MemoryAttribute(name="profession", type=str, default_or_value="", description="agent profession"),
        MemoryAttribute(name="interests", type=list, default_or_value=[], description="agent interests"),
        MemoryAttribute(name="profile", type=dict, default_or_value={}, description="profile info"),
        MemoryAttribute(name="sentiment", type=float, default_or_value=0.0, description="sentiment [-1,1]"),
        MemoryAttribute(name="emotion", type=str, default_or_value="Neutral", description="current emotion"),
        MemoryAttribute(name="thought", type=str, default_or_value="", description="agent reflection", whether_embedding=True),
        MemoryAttribute(name="adopted", type=bool, default_or_value=False, description="has adopted product"),
        MemoryAttribute(name="attitude", type=str, default_or_value="neutral", description="attitude toward product"),
        MemoryAttribute(name="current_need", type=str, default_or_value="none", description="current need"),
        MemoryAttribute(name="exposure_count", type=int, default_or_value=0, description="marketing exposures"),
        MemoryAttribute(name="messages_shared", type=int, default_or_value=0, description="forwarded messages"),
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
        self.logger = logging.getLogger(self.__class__.__name__)
        self.processed_msgs: set[Tuple[int, str]] = set()
        self.max_forwards = (
            agent_params.max_forwards if agent_params and hasattr(agent_params, "max_forwards") else 5
        )
        self.sentiment_adoption_threshold = (
            agent_params.sentiment_adoption_threshold
            if agent_params and hasattr(agent_params, "sentiment_adoption_threshold")
            else (
                agent_params.adoption_threshold
                if agent_params and hasattr(agent_params, "adoption_threshold")
                else 0.6
            )
        )

    async def init(self) -> None:
        await super().init()
        profile = await _extract_profile_from_memory(self.memory)
        connections = profile.get("connections", [])
        friends = [int(c.get("target")) for c in connections]
        await self.memory.status.update("profile", profile)
        await self.memory.status.update("friends", friends)
        ID_TO_PROFILE[self.id] = profile

    async def _handle_message(
        self, content: str, sender_id: int | None = None, tags: List[str] | None = None
    ) -> str:
        profile = await self.memory.status.get("profile") or {}
        sentiment = await self.memory.status.get("sentiment")
        adopted = await self.memory.status.get("adopted")
        friends = await self.memory.status.get("friends") or []
        friend_names = [ID_TO_PROFILE.get(f, {}).get("name", "") for f in friends if f in ID_TO_PROFILE]
        exposure = await self.memory.status.get("exposure_count") or 0
        (
            new_sentiment,
            new_adopted,
            say,
            llm_share,
            suggested,
            emotion,
            thought,
            attitude,
            need,
        ) = await _consult_llm(
            self, profile, sentiment, adopted, content, friend_names, exposure
        )
        new_sentiment = float(np.clip(new_sentiment, -1, 1))
        model = profile.get("share_model", "rule")
        if model != "llm":
            sim_self = _similarity(tags or [], profile)
            share = bool(RNG.random() < (0.3 + 0.5 * sim_self))
            suggested = []
        else:
            share = llm_share
        exposure += 1
        await self.memory.status.update("exposure_count", exposure)
        # fatigue: dampen change as exposures accumulate
        delta = new_sentiment - sentiment
        fatigue = float(np.exp(-0.3 * (exposure - 1)))
        effective_sentiment = sentiment + delta * fatigue
        if not new_adopted and effective_sentiment >= self.sentiment_adoption_threshold:
            new_adopted = True
        final_adopted = new_adopted
        await self.memory.status.update("sentiment", effective_sentiment)
        await self.memory.status.update("emotion", emotion)
        await self.memory.status.update("thought", thought)
        await self.memory.status.update("adopted", final_adopted)
        await self.memory.status.update("attitude", attitude)
        await self.memory.status.update("current_need", need)
        if self.database_writer is not None:
            emotion_score = EMOTION_SCORE_MAP.get(emotion.strip().lower(), 0.0)
            await self.database_writer.log_metric(
                [
                    (f"sentiment:{self.id}", float(effective_sentiment), exposure),
                    (f"adopted:{self.id}", 1.0 if final_adopted else 0.0, exposure),
                    (f"emotion:{self.id}", float(emotion_score), exposure),
                ]
            )
        if share:
            await self._share_message(say, tags or [], sender_id, suggested)
        return say

    async def _share_message(
        self, content: str, tags: List[str], exclude: int | None, suggested: List[str]
    ) -> None:
        friends = await self.memory.status.get("friends") or []
        if not friends:
            return
        profile = await self.memory.status.get("profile") or {}
        scores: List[Tuple[float, int]] = []
        for fid in friends:
            if exclude is not None and fid == exclude:
                continue
            strength = 0.5
            for conn in profile.get("connections", []):
                if conn["target"] == fid:
                    strength = float(conn.get("strength", 0.5))
                    break
            weight = strength
            friend_profile = ID_TO_PROFILE.get(fid, {})
            sim = _similarity(tags, friend_profile)
            weight *= 1 + BETA * sim
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
        shared = await self.memory.status.get("messages_shared") or 0
        shared += len(chosen)
        await self.memory.status.update("messages_shared", shared)
        for fid in chosen:
            await self.send_message_to_agent(fid, content)

    async def do_chat(self, message: Message) -> str:
        sender_id = message.from_id
        raw = str(message.payload)
        if not raw:
            return ""
        key = (sender_id or -1, raw)
        if key in self.processed_msgs:
            return ""
        self.processed_msgs.add(key)
        content = _extract_text(str(raw))

        if self.database_writer is not None:
            storage_dialog = StorageDialog(
                id=self.id,
                day=message.day,
                t=message.t,
                type=StorageDialogType.Talk,
                speaker=str(sender_id) if sender_id is not None else "",
                content=content,
                created_at=datetime.now(timezone.utc),
            )
            await self.database_writer.write_dialogs([storage_dialog])

        return await self._handle_message(content, sender_id, [])

    async def react_to_intervention(self, intervention_message: str):
        """Handle incoming marketing intervention payloads.

        The simulation engine sends marketing messages as a JSON string
        containing both the content and optional tags. Previously we passed
        the raw JSON string straight into ``_handle_message`` which ignored
        tags and gave the LLM a JSON blob instead of the human‑readable
        message. As a result agents rarely shared messages and no LLM tokens
        were consumed.

        This method now parses the JSON payload, extracting the ``content``
        and ``tags`` fields before delegating to ``_handle_message``. If the
        payload isn't valid JSON we fall back to treating the entire string as
        the message with no tags. This ensures marketing interventions always
        trigger cognitive processing and optional sharing behaviour.
        """

        try:
            data = json.loads(intervention_message)
            content = data.get("content", intervention_message)
            tags: List[str] = data.get("tags", []) if isinstance(data.get("tags"), list) else []
        except Exception:
            content = intervention_message
            tags = []

        await self._handle_message(content, None, tags)

    async def forward(self) -> None:
        """Execute one simulation tick.

        Marketing agents are reactive: they only process incoming messages.
        Thus, the default forward simply returns without additional action.
        """
        return
