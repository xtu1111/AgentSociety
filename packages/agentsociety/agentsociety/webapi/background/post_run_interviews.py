import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, cast

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from ...llm import LLM, LLMConfig
from ...commercial.billing.models import ExperimentBillConfig
from ...storage import DatabaseWriter
from ...storage.model import Experiment as StorageExperiment
from ...storage.type import (
    StorageDialog,
    StorageDialogType,
    StoragePendingDialog,
)
from ..models.experiment import ExperimentStatus
from ..models.config import LLMConfig as DBLLMConfig

logger = logging.getLogger(__name__)


@dataclass
class _SentimentSnapshot:
    value: float
    day: int
    t: float


class PostRunInterviewWorker:
    """Background worker that processes pending post-run interview dialogs."""

    def __init__(self, app: FastAPI, poll_interval: float = 5.0) -> None:
        self._app = app
        self._poll_interval = poll_interval
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        self._writers: dict[str, DatabaseWriter] = {}
        self._profiles: dict[str, Dict[int, Dict[str, Any]]] = {}
        self._llm_cache: dict[str, LLM] = {}

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="post-run-interview-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._process_cycle()
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Post-run interview worker cycle failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                continue

    async def _process_cycle(self) -> None:
        async with self._app.state.get_db() as session:  # type: ignore[attr-defined]
            db = cast(AsyncSession, session)
            stmt = select(StorageExperiment).where(
                StorageExperiment.status == ExperimentStatus.FINISHED.value
            )
            experiments = (await db.execute(stmt)).scalars().all()

        for experiment in experiments:
            exp_id = str(experiment.id)
            writer = self._writers.get(exp_id)
            if writer is None:
                writer = DatabaseWriter(
                    experiment.tenant_id,
                    exp_id,
                    self._app.state.env.db,  # type: ignore[attr-defined]
                    self._app.state.env.home_dir,  # type: ignore[attr-defined]
                )
                self._writers[exp_id] = writer
            try:
                pending_dialogs = await writer.fetch_pending_dialogs(
                    post_run=True, limit=20
                )
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Failed to load post-run dialogs for experiment %s", exp_id
                )
                continue

            if not pending_dialogs:
                continue

            profiles = await self._get_profiles(writer, exp_id)
            llm = await self._get_llm(experiment, exp_id)
            if llm is None:
                logger.warning(
                    "Skip processing post-run dialogs for %s because LLM config is missing",
                    exp_id,
                )
                continue

            for pending in pending_dialogs:
                try:
                    await self._handle_dialog(writer, llm, profiles, pending)
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception(
                        "Failed to handle post-run dialog %s for experiment %s",
                        pending.id,
                        exp_id,
                    )

    async def _get_llm(
        self, experiment: StorageExperiment, exp_id: str
    ) -> Optional[LLM]:
        cached = self._llm_cache.get(exp_id)
        if cached is not None:
            return cached
        config_dict: Optional[dict[str, Any]] = None
        raw = experiment.config or ""

        # Prefer direct JSON/YAML parsing; fall back to base64 for legacy data.
        if isinstance(raw, str):
            try:
                config_dict = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    parsed_yaml = yaml.safe_load(raw)
                    if isinstance(parsed_yaml, dict):
                        config_dict = parsed_yaml
                except yaml.YAMLError:
                    config_dict = None

        if config_dict is None:
            try:
                decoded = base64.b64decode(raw, validate=True)
                text = decoded.decode("utf-8")
                try:
                    config_dict = json.loads(text)
                except json.JSONDecodeError:
                    parsed_yaml = yaml.safe_load(text)
                    if isinstance(parsed_yaml, dict):
                        config_dict = parsed_yaml
            except (ValueError, UnicodeDecodeError, yaml.YAMLError):
                config_dict = None

        if not isinstance(config_dict, dict):
            logger.warning("Unable to deserialize config for experiment %s", exp_id)
            return None

        try:
            llm_config_dicts = await self._resolve_llm_configs(experiment, config_dict)
            if not llm_config_dicts:
                logger.warning("No usable LLM configuration for experiment %s", exp_id)
                return None

            deduped: list[dict[str, Any]] = []
            seen: set[tuple[Any, ...]] = set()
            for cfg in llm_config_dicts:
                if not isinstance(cfg, dict):
                    continue
                api_key = cfg.get("api_key")
                if not api_key:
                    continue
                fingerprint = (
                    cfg.get("provider"),
                    cfg.get("model"),
                    cfg.get("base_url"),
                    api_key,
                )
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                deduped.append(cfg)

            if not deduped:
                logger.warning("Resolved LLM configs for %s lack API keys", exp_id)
                return None

            llm_configs = [LLMConfig.model_validate(cfg) for cfg in deduped]
            llm = LLM(llm_configs)
            self._llm_cache[exp_id] = llm
            return llm
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Unable to construct LLM for experiment %s", exp_id)
            return None

    async def _resolve_llm_configs(
        self, experiment: StorageExperiment, config_dict: dict[str, Any]
    ) -> List[dict[str, Any]]:
        """Collect usable LLM configurations for an experiment."""

        exp_id = str(experiment.id)
        partials = config_dict.get("llm", [])
        resolved: List[dict[str, Any]] = []
        pending_lookup: List[dict[str, Any]] = []

        if isinstance(partials, list):
            for entry in partials:
                if not isinstance(entry, dict):
                    continue
                if entry.get("api_key"):
                    resolved.append(entry)
                else:
                    pending_lookup.append(entry)

        if not pending_lookup:
            return resolved

        try:
            async with self._app.state.get_db() as session:  # type: ignore[attr-defined]
                db = cast(AsyncSession, session)

                tenant_candidates = [experiment.tenant_id, "", "default"]

                # Prefer the exact config recorded for billing (if any).
                stmt = select(ExperimentBillConfig.llm_config_id).where(
                    ExperimentBillConfig.tenant_id == experiment.tenant_id,
                    ExperimentBillConfig.exp_id == experiment.id,
                )
                llm_config_id = (await db.execute(stmt)).scalar_one_or_none()

                candidate_configs: List[dict[str, Any]] = []

                if llm_config_id is not None:
                    stmt = select(DBLLMConfig.config).where(
                        DBLLMConfig.tenant_id.in_(tenant_candidates),
                        DBLLMConfig.id == llm_config_id,
                    )
                    cfg = (await db.execute(stmt)).scalar_one_or_none()
                    if cfg is not None:
                        if isinstance(cfg, list):
                            candidate_configs.extend(
                                [c for c in cfg if isinstance(c, dict)]
                            )
                        elif isinstance(cfg, dict):
                            candidate_configs.append(cfg)

                catalog_entries: List[dict[str, Any]] = []
                if pending_lookup:
                    stmt = select(DBLLMConfig.config).where(
                        DBLLMConfig.tenant_id.in_(tenant_candidates)
                    )
                    rows = (await db.execute(stmt)).scalars().all()
                    for row in rows:
                        if isinstance(row, list):
                            catalog_entries.extend(
                                [c for c in row if isinstance(c, dict)]
                            )
                        elif isinstance(row, dict):
                            catalog_entries.append(row)

                for partial in pending_lookup:
                    provider = partial.get("provider")
                    model = partial.get("model")
                    if not provider or not model:
                        continue
                    base_url = partial.get("base_url")

                    match = next(
                        (
                            cfg
                            for cfg in catalog_entries
                            if cfg.get("provider") == provider
                            and cfg.get("model") == model
                            and (
                                not base_url
                                or cfg.get("base_url") == base_url
                            )
                        ),
                        None,
                    )
                    if match:
                        candidate_configs.append(match)

        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "Failed to resolve LLM config from catalog for experiment %s", exp_id
            )
            candidate_configs = []

        for cfg in candidate_configs:
            if cfg.get("api_key"):
                resolved.append(cfg)

        return resolved

    async def _get_profiles(
        self, writer: DatabaseWriter, exp_id: str
    ) -> Dict[int, Dict[str, Any]]:
        cached = self._profiles.get(exp_id)
        if cached is not None:
            return cached
        try:
            rows = await writer.read_profiles()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to read profiles for experiment %s", exp_id)
            rows = []
        profile_map: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            profile = data.get("profile")
            if isinstance(profile, str):
                try:
                    profile = json.loads(profile)
                except json.JSONDecodeError:
                    pass
            data["profile"] = profile
            try:
                agent_id = int(data.get("id"))
            except (TypeError, ValueError):
                continue
            profile_map[agent_id] = data
        self._profiles[exp_id] = profile_map
        return profile_map

    async def _handle_dialog(
        self,
        writer: DatabaseWriter,
        llm: LLM,
        profiles: Dict[int, Dict[str, Any]],
        pending: StoragePendingDialog,
    ) -> None:
        agent_profile = profiles.get(pending.agent_id, {})
        question_text, preferred_language = _parse_dialog_content(pending.content)
        agent_name = agent_profile.get("name") or f"Agent {pending.agent_id}"
        history = await writer.read_dialogs(
            agent_id=pending.agent_id,
            order_direction="desc",
            limit=20,
        )
        history.reverse()
        history_lines = self._format_history(history, agent_profile)

        sentiments = await self._collect_sentiments(writer, pending.agent_id)
        sentiment_lines, last_sentiment = self._format_sentiment(sentiments)

        prompt_context = []
        if agent_profile:
            summary = {
                key: value
                for key, value in agent_profile.items()
                if key not in {"id", "profile"}
            }
            if summary:
                prompt_context.append("Profile: " + json.dumps(summary, ensure_ascii=False))
            if agent_profile.get("profile"):
                prompt_context.append(
                    "Attributes: "
                    + json.dumps(agent_profile["profile"], ensure_ascii=False)
                )
        if history_lines:
            prompt_context.append("Recent interactions:\n" + "\n".join(history_lines))
        else:
            prompt_context.append("Recent interactions: (none recorded)")
        if sentiment_lines:
            prompt_context.append("Sentiment timeline:\n" + "\n".join(sentiment_lines))
        elif last_sentiment is not None:
            prompt_context.append(
                f"Latest recorded sentiment: {last_sentiment.value:.2f} "
                f"(Day {last_sentiment.day}, t={last_sentiment.t:.0f})"
            )

        timeline_hint = f"Interview recorded for Day {pending.day} at t={pending.t:.0f}."

        dialog = [
            {
                "role": "system",
                "content": (
                    f"You are {agent_name}, a participant in a marketing simulation. "
                    "Answer interview questions in the first person, explain your reasoning, "
                    "and reference relevant experiences when helpful."
                ),
            },
        ]

        if preferred_language and preferred_language.lower() in {"ja", "ja-jp", "japanese"}:
            dialog.append(
                {
                    "role": "system",
                    "content": "必ず自然な日本語で回答してください。質問が他の言語で書かれていても、日本語で丁寧に答えてください。",
                }
            )

        dialog.append(
            {
                "role": "user",
                "content": (
                    "\n\n".join(prompt_context)
                    + "\n\n"
                    + timeline_hint
                    + "\nThe interviewer asks: "
                    + question_text
                ),
            }
        )

        response_text: Optional[str] = None
        try:
            response_text = await llm.atext_request(dialog=dialog, max_tokens=300)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "LLM failed to answer post-run dialog %s for experiment %s",
                pending.id,
                pending.agent_id,
            )

        if not response_text:
            response_text = (
                "I'm unable to provide a detailed answer right now, but I will review the situation later."
            )

        await writer.write_dialogs(
            [
                StorageDialog(
                    id=pending.agent_id,
                    day=pending.day,
                    t=pending.t,
                    type=StorageDialogType.User,
                    speaker="user",
                    content=question_text,
                    created_at=datetime.now(timezone.utc),
                ),
                StorageDialog(
                    id=pending.agent_id,
                    day=pending.day,
                    t=pending.t,
                    type=StorageDialogType.User,
                    speaker="",
                    content=response_text,
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await writer.mark_dialogs_as_processed([pending.id])

    async def _collect_sentiments(
        self, writer: DatabaseWriter, agent_id: int
    ) -> List[_SentimentSnapshot]:
        try:
            statuses = await writer.read_statuses(
                agent_id=agent_id,
                order_direction="desc",
                limit=10,
            )
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to read statuses for agent %s", agent_id)
            return []
        snapshots: List[_SentimentSnapshot] = []
        for row in statuses:
            value = self._extract_sentiment(row.get("status"))
            if value is None:
                continue
            snapshots.append(
                _SentimentSnapshot(
                    value=value,
                    day=int(row.get("day", 0)),
                    t=float(row.get("t", 0.0)),
                )
            )
        snapshots.sort(key=lambda s: (s.day, s.t))
        return snapshots

    def _format_sentiment(
        self, snapshots: List[_SentimentSnapshot]
    ) -> tuple[List[str], Optional[_SentimentSnapshot]]:
        if not snapshots:
            return [], None
        lines: List[str] = []
        last = snapshots[-1]
        previous_change = next(
            (
                snapshots[i]
                for i in range(len(snapshots) - 2, -1, -1)
                if abs(snapshots[i].value - last.value) > 1e-6
            ),
            None,
        )
        for snap in snapshots[-5:]:
            lines.append(
                f"Day {snap.day} t={snap.t:.0f}: sentiment {snap.value:.2f}"
            )
        if previous_change is not None:
            delta = last.value - previous_change.value
            lines.append(
                f"Most recent change: Δ{delta:.2f} between Day {previous_change.day} t={previous_change.t:.0f} "
                f"and Day {last.day} t={last.t:.0f}."
            )
        return lines, last

    def _format_history(
        self,
        history: List[Dict[str, Any]],
        profile: Dict[str, Any],
    ) -> List[str]:
        lines: List[str] = []
        for item in history:
            speaker_label = self._resolve_speaker(item, profile)
            content = self._extract_content(item.get("content"))
            lines.append(
                f"Day {item.get('day', 0)} t={item.get('t', 0)} {speaker_label}: {content}"
            )
        return lines[-10:]

    def _resolve_speaker(
        self, item: Dict[str, Any], profile: Dict[str, Any]
    ) -> str:
        dialog_type_value = item.get("type")
        dialog_type: Optional[StorageDialogType] = None
        try:
            dialog_type = StorageDialogType(dialog_type_value)
        except Exception:
            dialog_type = None
        speaker = item.get("speaker")
        if dialog_type == StorageDialogType.User:
            if speaker == "user":
                return "User"
            return profile.get("name") or "Agent"
        if speaker:
            return str(speaker)
        return profile.get("name") or "Agent"

    def _extract_content(self, raw: Any) -> str:
        if isinstance(raw, str):
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict) and "content" in loaded:
                    return str(loaded["content"])
            except json.JSONDecodeError:
                pass
            return raw
        return json.dumps(raw, ensure_ascii=False)

    def _extract_sentiment(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    return None
                return self._extract_sentiment(parsed)
        if isinstance(value, dict):
            if "sentiment" in value:
                return self._extract_sentiment(value.get("sentiment"))
            if "status" in value:
                return self._extract_sentiment(value.get("status"))
        return None


def create_post_run_worker(app: FastAPI) -> PostRunInterviewWorker:
    worker = PostRunInterviewWorker(app)
    worker.start()
    return worker


def _parse_dialog_content(raw: str) -> Tuple[str, Optional[str]]:
    if not raw:
        return "", None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw, None
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        language = parsed.get("preferred_language")
        if isinstance(language, str) and language:
            return parsed["content"], language
        return parsed["content"], None
    return raw, None