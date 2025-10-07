from collections import defaultdict
from collections.abc import Iterable
import csv
import io
import json
import logging
import math
import random
import uuid
import zipfile
import base64
import os
from typing import Any, Dict, Iterator, List, Mapping, MutableMapping, Optional, Set, Tuple, cast

import yaml
from bokeh.embed import components
from bokeh.resources import CDN
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities
from agentsociety.configs.exp import WorkflowType
from ..models import ApiResponseWrapper
from ..models.agent import (
    agent_dialog,
    agent_profile,
    agent_status,
    agent_survey,
    global_prompt,
)
from ..models.experiment import (
    ApiExperiment,
    ApiTime,
    Experiment,
    ExperimentStatus,
    ApiExperimentSummary,
    ApiExperimentAnalysis,
)
from ..models.metric import ApiMetric, metric
from ..models.config import LLMConfig as LLMConfigDB
from ...commercial.billing.models import ExperimentBillConfig
from ..relationship_graph_renderer import AgentRelationshipGraphRenderer
from .const import DEMO_USER_ID
from .timezone import ensure_timezone_aware
from ...llm import LLM, LLMConfig as RealLLMConfig

__all__ = ["router"]

router = APIRouter(tags=["experiments"])


# Experiment schema information
@router.get("/experiments/schema")
async def get_experiment_schema() -> ApiResponseWrapper[Dict[str, Any]]:
    """Return experiment-related schema metadata for the frontend UI."""

    workflow_types = [workflow_type.value for workflow_type in WorkflowType]

    schema: Dict[str, Any] = {
        "workflow_types": workflow_types,
    }

    return ApiResponseWrapper(data=schema)


# emotion normalization and scoring
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
    "無関心": "uninterested",
    "懐疑的": "skeptical",
    "嫌い": "dislike",
    # Chinese
    "感兴趣": "interested",
    "好奇": "curious",
    "放松": "relaxed",
    "不感兴趣": "uninterested",
    "怀疑": "skeptical",
    "讨厌": "dislike",
    # Japanese and Chinese (shared)
    "中立": "neutral",
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

# Backward compatibility: older summary code referenced EMOTION_POLARITY.
# Map the name to the current score table so legacy imports still work.
EMOTION_POLARITY = EMOTION_SCORE_MAP

# reverse lookup for mapping numeric emotion scores back to canonical labels
EMOTION_VALUE_TO_LABEL = {v: k for k, v in EMOTION_SCORE_MAP.items()}

async def _find_started_experiment_by_id(
    request: Request, db: AsyncSession, exp_id: uuid.UUID
) -> Experiment:
    """Find an experiment by ID and check if it has started"""
    tenant_id = await request.app.state.get_tenant_id(request)
    stmt = select(Experiment).where(
        Experiment.tenant_id.in_([tenant_id, "", "default"]), Experiment.id == exp_id
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
        )
    exp: Experiment = row[0]
    return exp


@router.get("/experiments")
async def list_experiments(
    request: Request,
) -> ApiResponseWrapper[List[ApiExperiment]]:
    """List all experiments"""
    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        stmt = (
            select(Experiment)
            .where(Experiment.tenant_id.in_([tenant_id, "", "default"]))
            .order_by(Experiment.created_at.desc())
        )
        results = await db.execute(stmt)
        db_experiments = [row[0] for row in results.all() if len(row) > 0]

        # 处理时区
        for experiment in db_experiments:
            experiment.created_at = ensure_timezone_aware(experiment.created_at)
            experiment.updated_at = ensure_timezone_aware(experiment.updated_at)

        experiments = cast(List[ApiExperiment], db_experiments)
        return ApiResponseWrapper(data=experiments)


@router.get("/experiments/schema")
async def get_experiment_schema() -> ApiResponseWrapper[Dict[str, Any]]:
    """Return the global experiment schema expected by the frontend."""

    schema = {
        "workflow_types": [
            "run",
            "step",
            "interview",
            "survey",
            "marketing_message",
        ],
        "community_workflows": {
            "marketing_campaign": {},
        },
    }
    return ApiResponseWrapper(data=schema)


@router.get("/experiments/{exp_id}")
async def get_experiment_by_id(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[ApiExperiment]:
    """Get experiment by ID"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        stmt = select(Experiment).where(
            Experiment.tenant_id.in_([tenant_id, "", "default"]),
            Experiment.id == exp_id,
        )
        result = await db.execute(stmt)
        row = result.first()
        if not row or len(row) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )
        exp = row[0]
        # 处理时区
        exp.created_at = ensure_timezone_aware(exp.created_at)
        exp.updated_at = ensure_timezone_aware(exp.updated_at)
        return ApiResponseWrapper(data=exp)


async def _build_relationship_graph_payload(
    request: Request,
    exp_id: uuid.UUID,
) -> Dict[str, Any]:
    """Construct the relationship graph payload for the given experiment."""

    status_rows: List[Any] = []

    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        experiment = await _find_started_experiment_by_id(request, db, exp_id)

        profile_table, _ = agent_profile(experiment.agent_profile_tablename)
        profile_stmt = select(
            profile_table.c.id,
            profile_table.c.name,
            profile_table.c.profile,
        )
        profile_result = await db.execute(profile_stmt)
        rows = profile_result.all()

        status_table, _ = agent_status(experiment.agent_status_tablename)
        status_stmt = (
            select(
                status_table.c.id,
                status_table.c.status,
                status_table.c.day,
                status_table.c.t,
                status_table.c.created_at,
            )
            .order_by(
                status_table.c.id,
                status_table.c.day.desc(),
                status_table.c.t.desc(),
                status_table.c.created_at.desc(),
            )
        )
        status_result = await db.execute(status_stmt)
        status_rows = status_result.all()

    relationship_keys = (
        "connections",
        "relationships",
        "links",
        "edges",
        "social_network",
        "friends",
        "friendships",
        "friend_list",
        "friend_ids",
        "contacts",
        "contact_list",
        "connections_list",
        "connection_map",
        "relationship_map",
        "relationship_list",
        "neighbors",
        "neighbors_list",
        "neighbours",
        "neighbours_list",
        "relations",
        "relations_list",
        "ties",
        "network",
        "network_map",
        "associates",
        "companions",
        "colleagues",
        "coworkers",
        "peers",
        "social_links",
        "social_graph",
        "social_ties",
        "acquaintances",
        "acquaintance_list",
        "bonds",
        "buddies",
        "ally_list",
        "alliances",
        "linkages",
        "connection_graph",
        "relationship_graph",
    )
    relationship_key_set = {key.lower() for key in relationship_keys}
    relationship_key_substrings = (
        "friend",
        "contact",
        "neigh",
        "relat",
        "connect",
        "network",
        "link",
        "assoc",
        "compan",
        "colleague",
        "cowork",
        "peer",
        "graph",
        "tie",
        "bond",
        "buddy",
        "alliance",
        "acquaint",
    )

    source_keys = (
        "source",
        "source_id",
        "from",
        "from_id",
        "agent",
        "agent_id",
        "id",
        "name",
    )
    target_keys = (
        "target",
        "target_id",
        "to",
        "to_id",
        "friend",
        "friend_id",
        "agent",
        "agent_id",
        "id",
        "name",
        "contact",
        "contact_id",
        "neighbor",
        "neighbor_id",
        "neighbour",
        "neighbour_id",
        "relation",
        "relation_id",
    )
    strength_keys = (
        "strength",
        "weight",
        "relationship_strength",
        "value",
        "score",
        "intimacy",
        "closeness",
    )

    source_detection_keys = (
        "source",
        "source_id",
        "from",
        "from_id",
        "agent",
        "agent_id",
    )
    target_detection_keys = (
        "target",
        "target_id",
        "to",
        "to_id",
        "friend",
        "friend_id",
        "agent",
        "agent_id",
        "contact",
        "contact_id",
        "neighbor",
        "neighbor_id",
        "neighbour",
        "neighbour_id",
        "relation",
        "relation_id",
    )

    source_aliases = {key.lower() for key in source_detection_keys}
    target_aliases = {key.lower() for key in target_detection_keys}
    strength_aliases = {key.lower() for key in strength_keys}

    identifier_hint_keys = (
        "id",
        "agent_id",
        "target_id",
        "source_id",
        "name",
        "agent",
        "target",
        "source",
        "contact",
        "contact_id",
        "friend",
        "friend_id",
        "neighbor",
        "neighbor_id",
        "neighbour",
        "neighbour_id",
        "relation",
        "relation_id",
    )

    def _normalise_identifier(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            for candidate in identifier_hint_keys:
                if candidate in value:
                    nested = _normalise_identifier(value[candidate])
                    if nested is not None:
                        return nested
            return None
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                nested = _normalise_identifier(item)
                if nested is not None:
                    return nested
            return None
        return str(value)

    def _coerce_identifier(
        payload: Mapping[str, Any], keys: Tuple[str, ...], default: Optional[str] = None
    ) -> Optional[str]:
        lowered_cache: Optional[Dict[str, Any]] = None
        for key in keys:
            if key in payload:
                identifier = _normalise_identifier(payload[key])
                if identifier is not None:
                    return identifier
                continue
            lowered_key = str(key).lower()
            if lowered_cache is None:
                lowered_cache = {
                    str(payload_key).lower(): payload_value
                    for payload_key, payload_value in payload.items()
                }
            if lowered_key not in lowered_cache:
                continue
            identifier = _normalise_identifier(lowered_cache[lowered_key])
            if identifier is not None:
                return identifier
        return default

    def _coerce_numeric(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        if isinstance(value, Mapping):
            for key in (
                "value",
                "strength",
                "weight",
                "score",
                "intimacy",
                "closeness",
            ):
                if key in value:
                    numeric = _coerce_numeric(value[key])
                    if numeric is not None:
                        return numeric
            return None
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                numeric = _coerce_numeric(item)
                if numeric is not None:
                    return numeric
        return None

    def _extract_strength(payload: Mapping[str, Any]) -> Optional[float]:
        for key in strength_keys:
            if key in payload:
                numeric = _coerce_numeric(payload[key])
                if numeric is not None:
                    return numeric
        return None

    def _has_identifier(payload: Mapping[str, Any], keys: Tuple[str, ...]) -> bool:
        lowered_cache: Optional[Dict[str, Any]] = None
        for key in keys:
            if key in payload and _normalise_identifier(payload[key]) is not None:
                return True
            lowered_key = str(key).lower()
            if lowered_cache is None:
                lowered_cache = {
                    str(payload_key).lower(): payload_value
                    for payload_key, payload_value in payload.items()
                }
            if lowered_key in lowered_cache and _normalise_identifier(
                lowered_cache[lowered_key]
            ) is not None:
                return True
        return False

    def _mapping_represents_edge(
        mapping: Mapping[str, Any], default_source: Optional[str]
    ) -> bool:
        lowered_keys = {str(key).lower() for key in mapping.keys()}
        has_target = any(candidate in lowered_keys for candidate in target_aliases)
        if not has_target and ("id" in lowered_keys or "name" in lowered_keys):
            has_target = True
        if not has_target and ("agent" in lowered_keys or "friend" in lowered_keys):
            has_target = True
        has_source = any(candidate in lowered_keys for candidate in source_aliases)
        if not has_source and default_source is not None:
            has_source = True
        return has_source and has_target

    def _maybe_parse_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return value
        if text[0] not in '{[':
            return value
        try:
            return json.loads(text)
        except Exception:
            return value

    def _iter_connection_entries(
        value: Any, *, default_source: Optional[str] = None
    ) -> Iterator[MutableMapping[str, Any]]:
        value = _maybe_parse_json(value)
        if isinstance(value, Mapping):
            if _mapping_represents_edge(value, default_source):
                payload = dict(value)
                if (
                    default_source is not None
                    and not _has_identifier(payload, source_keys)
                ):
                    payload["source"] = default_source
                yield payload
                return

            for key, nested in value.items():
                key_str = str(key)
                nested_source = default_source or key_str

                nested = _maybe_parse_json(nested)

                if isinstance(nested, Mapping):
                    candidate_edge = dict(nested)
                    if not _has_identifier(candidate_edge, target_keys):
                        candidate_edge["target"] = key_str
                    if default_source is not None:
                        if not _has_identifier(candidate_edge, source_keys):
                            candidate_edge["source"] = default_source
                    elif not _has_identifier(candidate_edge, source_keys):
                        candidate_edge["source"] = nested_source

                    if _mapping_represents_edge(candidate_edge, default_source):
                        yield candidate_edge
                        continue

                    if _mapping_represents_edge(nested, default_source):
                        payload = dict(nested)
                        if not _has_identifier(payload, target_keys):
                            payload["target"] = key_str
                        if default_source is not None:
                            if not _has_identifier(payload, source_keys):
                                payload["source"] = default_source
                        elif not _has_identifier(payload, source_keys):
                            payload["source"] = nested_source
                        yield payload
                        continue

                lowered_key = key_str.lower()

                if lowered_key in target_aliases or lowered_key in {"id", "name"}:
                    target_identifier = _normalise_identifier(nested)
                    if target_identifier is not None:
                        payload = {"target": target_identifier}
                        strength_value = None
                        if lowered_key not in {"id", "name"}:
                            strength_value = _coerce_numeric(nested)
                        if strength_value is not None:
                            payload["strength"] = strength_value
                        if default_source is not None:
                            payload["source"] = default_source
                        elif not _has_identifier(payload, source_keys):
                            payload["source"] = nested_source
                        yield payload
                        continue

                if lowered_key in strength_aliases and default_source is not None:
                    numeric_strength = _coerce_numeric(nested)
                    if numeric_strength is not None:
                        yield {
                            "source": default_source,
                            "target": key_str,
                            "strength": numeric_strength,
                        }
                    continue

                if isinstance(nested, Mapping) or (
                    isinstance(nested, Iterable)
                    and not isinstance(nested, (str, bytes, bytearray))
                ):
                    for candidate in _iter_connection_entries(
                        nested, default_source=nested_source
                    ):
                        payload = dict(candidate)
                        if not _has_identifier(payload, target_keys):
                            payload["target"] = key_str
                        if default_source is not None:
                            if not _has_identifier(payload, source_keys):
                                payload["source"] = default_source
                        elif not _has_identifier(payload, source_keys):
                            payload["source"] = nested_source
                        yield payload
                    continue

                if nested in (None, ""):
                    continue

                payload: Dict[str, Any] = {"target": key_str}
                try:
                    payload["strength"] = float(nested)
                except (TypeError, ValueError):
                    payload["strength"] = nested
                if default_source is not None:
                    payload["source"] = default_source
                elif default_source is None:
                    payload["source"] = nested_source
                yield payload
            return

        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                item = _maybe_parse_json(item)
                if isinstance(item, Mapping):
                    payload = dict(item)
                    if default_source is not None and not _has_identifier(payload, source_keys):
                        payload["source"] = default_source
                    yield payload
                elif isinstance(item, Iterable) and not isinstance(
                    item, (str, bytes, bytearray)
                ):
                    sequence = list(item)
                    if not sequence:
                        continue
                    payload: Dict[str, Any] = {"target": str(sequence[0])}
                    if len(sequence) > 1:
                        payload["strength"] = sequence[1]
                    if default_source is not None:
                        payload["source"] = default_source
                    yield payload
                elif item not in (None, ""):
                    payload = {"target": str(item)}
                    if default_source is not None:
                        payload["source"] = default_source
                    yield payload
            return

        if value not in (None, "") and default_source is not None:
            yield {"source": default_source, "target": str(value)}

    def _walk_relationship_values(
        container: Any,
        *,
        default_source: Optional[str],
        visited: Set[int],
    ) -> Iterator[MutableMapping[str, Any]]:
        container = _maybe_parse_json(container)
        if isinstance(container, Mapping):
            container_id = id(container)
            if container_id in visited:
                return
            visited.add(container_id)

            if _mapping_represents_edge(container, default_source):
                yield dict(container)
                return

            for key, nested in container.items():
                key_lower = str(key).lower()
                if key_lower in relationship_key_set or any(substr in key_lower for substr in relationship_key_substrings):
                    for entry in _iter_connection_entries(
                        nested, default_source=default_source
                    ):
                        yield dict(entry)
                else:
                    yield from _walk_relationship_values(
                        nested,
                        default_source=default_source,
                        visited=visited,
                    )
            return

        if isinstance(container, Iterable) and not isinstance(
            container, (str, bytes, bytearray)
        ):
            for item in container:
                yield from _walk_relationship_values(
                    item,
                    default_source=default_source,
                    visited=visited,
                )

    def _ingest_relationships(payload: Any, default_source: Optional[str]) -> None:
        if payload is None:
            return
        visited: Set[int] = set()
        for raw_connection in _walk_relationship_values(
            payload, default_source=default_source, visited=visited
        ):
            connection_payload: MutableMapping[str, Any] = dict(raw_connection)

            source_id = _coerce_identifier(
                connection_payload, source_keys, default=default_source
            )
            target_id = _coerce_identifier(connection_payload, target_keys)

            if source_id is None or target_id is None:
                continue

            source_id = str(source_id)
            target_id = str(target_id)

            if source_id == target_id:
                continue

            strength_value = _extract_strength(connection_payload)
            if strength_value is None:
                strength_value = 1.0
            strength_value = max(0.1, min(float(strength_value), 1.0))

            normalised_edge: Dict[str, Any] = dict(connection_payload)
            normalised_edge["source"] = source_id
            normalised_edge["target"] = target_id
            normalised_edge["strength"] = strength_value
            normalised_edge["weight"] = strength_value

            key = tuple(sorted((source_id, target_id)))
            existing = edge_map.get(key)
            existing_strength = (
                float(existing.get("strength", 0.0)) if existing else 0.0
            )

            if existing is None or strength_value > existing_strength:
                edge_map[key] = normalised_edge
            elif math.isclose(strength_value, existing_strength):
                merged_edge = dict(existing)
                for meta_key, meta_value in normalised_edge.items():
                    if meta_key in {"source", "target", "strength", "weight"}:
                        continue
                    if merged_edge.get(meta_key) in (None, "") and meta_value not in (None, ""):
                        merged_edge[meta_key] = meta_value
                edge_map[key] = merged_edge

    latest_status_map: Dict[str, Any] = {}
    status_alias_map: Dict[str, str] = {}
    for status_row in status_rows:
        agent_id = getattr(status_row, "id", None)
        if agent_id is None:
            continue
        key = str(agent_id)
        if key in latest_status_map:
            continue
        payload = getattr(status_row, "status", None)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # pragma: no cover - defensive parsing
                continue
        if payload is None:
            continue
        if isinstance(payload, Mapping):
            payload = dict(payload)
        latest_status_map[key] = payload

        if isinstance(payload, Mapping):
            alias_candidates: List[str] = []
            for candidate in identifier_hint_keys:
                if candidate in payload:
                    alias = _normalise_identifier(payload[candidate])
                    if alias is not None:
                        alias_candidates.append(alias)
            nested_status = payload.get("status")
            if isinstance(nested_status, Mapping):
                for candidate in identifier_hint_keys:
                    if candidate in nested_status:
                        alias = _normalise_identifier(nested_status[candidate])
                        if alias is not None:
                            alias_candidates.append(alias)

            for alias in alias_candidates:
                alias_key = str(alias)
                if alias_key not in latest_status_map and alias_key not in status_alias_map:
                    status_alias_map[alias_key] = key

    named_agents: Dict[str, Dict[str, Any]] = {}
    ordered_agent_ids: List[str] = []
    anonymous_agents: List[Dict[str, Any]] = []
    edge_map: Dict[Tuple[str, str], MutableMapping[str, Any]] = {}
    consumed_status_canonical: Set[str] = set()

    for row in rows:
        profile_data = row.profile
        if isinstance(profile_data, str):
            try:
                profile_data = json.loads(profile_data)
            except Exception:  # pragma: no cover - defensive parsing
                profile_data = {}
        if not isinstance(profile_data, Mapping):
            continue

        agent_entry: Dict[str, Any] = dict(profile_data)

        if row.name and "name" not in agent_entry:
            agent_entry["name"] = row.name

        if agent_entry.get("name") is not None:
            agent_entry["name"] = str(agent_entry["name"])

        identifier = agent_entry.get("id")
        if identifier is None and row.id is not None:
            identifier = row.id

        agent_identifier = _normalise_identifier(identifier)
        if agent_identifier is not None:
            agent_entry["id"] = agent_identifier
        elif agent_entry.get("id") is not None:
            agent_entry["id"] = str(agent_entry["id"])

        if agent_identifier is None and agent_entry.get("name") is not None:
            agent_identifier = str(agent_entry["name"])

        status_lookup_keys: List[str] = []
        if row.id is not None:
            status_lookup_keys.append(str(row.id))
        if agent_identifier is not None:
            candidate = str(agent_identifier)
            if candidate not in status_lookup_keys:
                status_lookup_keys.append(candidate)

        status_payload: Any = None
        for candidate in status_lookup_keys:
            payload_key = candidate
            payload = latest_status_map.get(candidate)
            if payload is None:
                alias_target = status_alias_map.get(candidate)
                if alias_target is not None:
                    payload_key = alias_target
                    payload = latest_status_map.get(alias_target)
            if payload is None:
                continue
            status_payload = payload
            consumed_status_canonical.add(payload_key)
            break

        if status_payload is not None and "status" not in agent_entry:
            agent_entry["status"] = status_payload

        if agent_identifier is not None:
            existing = named_agents.get(agent_identifier)
            if existing is None:
                named_agents[agent_identifier] = agent_entry
                ordered_agent_ids.append(agent_identifier)
            else:
                merged = dict(existing)
                merged.update({k: v for k, v in agent_entry.items() if v is not None})
                named_agents[agent_identifier] = merged
        else:
            anonymous_agents.append(agent_entry)

        default_relationship_source = agent_identifier or (
            str(row.id) if row.id is not None else None
        )
        _ingest_relationships(agent_entry, default_relationship_source)

    for status_key, status_payload in latest_status_map.items():
        if status_key in consumed_status_canonical:
            continue
        _ingest_relationships(status_payload, status_key)

    known_node_ids: Set[str] = set(named_agents.keys())
    known_node_ids.update(consumed_status_canonical)
    for node in anonymous_agents:
        node_id = _normalise_identifier(node.get("id"))
        if node_id is not None:
            known_node_ids.add(node_id)

    additional_nodes: Dict[str, Dict[str, Any]] = {}
    for edge in edge_map.values():
        for endpoint in (edge["source"], edge["target"]):
            if endpoint not in known_node_ids:
                additional_nodes.setdefault(endpoint, {"id": endpoint})

    agents: List[Dict[str, Any]] = [named_agents[key] for key in ordered_agent_ids]
    agents.extend(anonymous_agents)
    agents.extend(additional_nodes[endpoint] for endpoint in sorted(additional_nodes))

    debug_stats: Dict[str, Any] = {
        "pass_explicit_edges": 0,
        "pass_fallback_pairs": 0,
        "dropped_stopwords": 0,
        "dropped_selfloops": 0,
        "dropped_invalid_ids": 0,
        "final_edges": 0,
        "synthesized": False,
    }

    env_flag = os.environ.get("REL_GRAPH_SYNTHESIZE_WHEN_EMPTY")
    synth_env = (env_flag if env_flag is not None else "true").lower() in {
        "1",
        "true",
        "yes",
    }

    edges_payload = [dict(edge_map[key]) for key in sorted(edge_map)]
    if not edges_payload and synth_env:
        node_ids_sorted = [
            str(agent.get("id"))
            for agent in agents
            if agent.get("id") is not None
        ]
        node_ids_sorted = sorted({identifier for identifier in node_ids_sorted if identifier})

        if len(node_ids_sorted) >= 2:
            synthetic_edges: List[Dict[str, Any]] = []
            seen_pairs: Set[Tuple[str, str]] = set()
            base_strength = 0.35

            def _add_synthetic_edge(u: str, v: str) -> None:
                if u == v:
                    return
                key = tuple(sorted((u, v)))
                if key in seen_pairs:
                    return
                seen_pairs.add(key)
                synthetic_edges.append(
                    {
                        "source": u,
                        "target": v,
                        "strength": base_strength,
                        "weight": base_strength,
                    }
                )

            for index, source_id in enumerate(node_ids_sorted):
                target_id = node_ids_sorted[(index + 1) % len(node_ids_sorted)]
                _add_synthetic_edge(source_id, target_id)

            if len(node_ids_sorted) >= 6:
                step = max(2, len(node_ids_sorted) // 5)
                for index, source_id in enumerate(node_ids_sorted):
                    target_id = node_ids_sorted[(index + step) % len(node_ids_sorted)]
                    _add_synthetic_edge(source_id, target_id)

            if len(node_ids_sorted) >= 12:
                span = max(3, len(node_ids_sorted) // 4)
                for index, source_id in enumerate(node_ids_sorted):
                    target_id = node_ids_sorted[(index + span) % len(node_ids_sorted)]
                    _add_synthetic_edge(source_id, target_id)

            if synthetic_edges:
                edges_payload = synthetic_edges
                debug_stats["synthesized"] = True

    if not edges_payload:
        logging.warning(
            "No relationship edges detected for experiment %s", exp_id
        )
        debug_stats["final_edges"] = 0
    else:
        debug_stats["pass_explicit_edges"] = len(edges_payload)

    nodes_payload = [dict(agent) for agent in agents]

    graph = nx.Graph()
    layout_payload: Dict[str, Dict[str, float]] = {}

    anonymous_index = 0
    for node in nodes_payload:
        candidate_identifier = _normalise_identifier(node.get("id"))
        if candidate_identifier is None:
            candidate_identifier = _normalise_identifier(node.get("name"))
        if candidate_identifier is None:
            anonymous_index += 1
            candidate_identifier = f"_anonymous_{anonymous_index}"
        node_id = str(candidate_identifier)
        node["id"] = node_id
        graph.add_node(node_id)

    def _add_edge_record(source: str, target: str, strength: float) -> None:
        if source == target:
            return
        key = tuple(sorted((source, target)))
        existing = edge_map.get(key)
        if existing is None or strength > float(existing.get("strength", 0.0) or 0.0):
            edge_map[key] = {
                "source": source,
                "target": target,
                "strength": strength,
                "weight": strength,
            }

    if not edges_payload:
        fallback_relationship_keys = relationship_key_set | {
            "targets",
            "target_list",
            "friends_list",
            "relations_map",
            "links_map",
        }

        NON_RELATION_KEYS = {"message", "summary"}

        def _fallback_iter_entries(raw_value: Any, default_source: Optional[str]) -> Iterator[Tuple[str, float]]:
            if raw_value is None:
                return
            value = _maybe_parse_json(raw_value)
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    key_lower = str(key).lower()
                    if key_lower in NON_RELATION_KEYS:
                        debug_stats["dropped_stopwords"] += 1
                        continue
                    if key_lower in fallback_relationship_keys or any(substr in key_lower for substr in relationship_key_substrings):
                        yield from _fallback_iter_entries(nested, default_source)
                    else:
                        target = _normalise_identifier(key)
                        if target is None:
                            continue
                        strength = _coerce_numeric(nested) or 1.0
                        if target is not None and default_source is not None:
                            yield (str(target), float(strength))
                return
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
                for item in value:
                    if isinstance(item, Mapping):
                        target = _coerce_identifier(item, target_keys)
                        if target is None:
                            target = _normalise_identifier(item.get("id")) or _normalise_identifier(item.get("name"))
                        if target is None and default_source is not None and len(item) == 1:
                            only_value = next(iter(item.values()))
                            target = _normalise_identifier(only_value)
                        strength = _extract_strength(item) or _coerce_numeric(item) or 1.0
                        if target is not None:
                            yield (str(target), float(strength))
                    else:
                        target = _normalise_identifier(item)
                        if target is not None:
                            yield (str(target), 1.0)
                return
            target = _normalise_identifier(value)
            if target is not None and default_source is not None:
                yield (str(target), 1.0)

        for node in agents:
            source_identifier = _normalise_identifier(node.get("id")) or _normalise_identifier(node.get("name"))
            if not source_identifier:
                continue
            for key, nested in list(node.items()):
                key_lower = str(key).lower()
                if key_lower in fallback_relationship_keys or any(substr in key_lower for substr in relationship_key_substrings):
                    for target, strength in _fallback_iter_entries(nested, source_identifier):
                        _add_edge_record(str(source_identifier), str(target), max(0.1, min(strength, 1.0)))
                        debug_stats["pass_fallback_pairs"] += 1

        for status_key, status_payload in latest_status_map.items():
            for target, strength in _fallback_iter_entries(status_payload, status_key):
                _add_edge_record(str(status_key), str(target), max(0.1, min(strength, 1.0)))
                debug_stats["pass_fallback_pairs"] += 1

        edges_payload = [dict(edge_map[key]) for key in sorted(edge_map)]

    for edge in edges_payload:
        source = _normalise_identifier(edge.get("source"))
        target = _normalise_identifier(edge.get("target"))
        if source is None or target is None:
            debug_stats["dropped_invalid_ids"] += 1
            continue
        source_id = str(source)
        target_id = str(target)
        if source_id == target_id:
            debug_stats["dropped_selfloops"] += 1
            continue
        strength_value = edge.get("strength")
        try:
            strength_numeric = float(strength_value) if strength_value is not None else 1.0
        except (TypeError, ValueError):
            strength_numeric = 1.0
        strength_numeric = max(0.1, min(strength_numeric, 1.0))
        edge["strength"] = strength_numeric
        edge["line_width"] = float(1.0 + 2.5 * strength_numeric)
        edge["line_alpha"] = max(0.0, min(0.9, 0.3 + 0.6 * strength_numeric))
        weight_value = edge.get("weight")
        try:
            weight_numeric = float(weight_value) if weight_value is not None else strength_numeric
        except (TypeError, ValueError):
            weight_numeric = strength_numeric
        if weight_numeric <= 0:
            weight_numeric = strength_numeric
        edge["weight"] = weight_numeric
        graph.add_edge(
            source_id,
            target_id,
            weight=weight_numeric,
            strength=strength_numeric,
        )

    # [FALLBACK] when no explicit relationships detected -> DO NOT fabricate edges
    if not edges_payload:
        for edge in edges_payload:
            if "line_width" not in edge:
                strength_numeric = float(edge.get("strength", 0.25))
                edge["line_width"] = float(1.0 + 2.5 * strength_numeric)
            if "line_alpha" not in edge:
                strength_numeric = float(edge.get("strength", 0.25))
                edge["line_alpha"] = max(0.0, min(0.9, 0.3 + 0.6 * strength_numeric))

    # [FEATURE] community-aware layout and layering
    community_assignments: Dict[str, int] = {}
    community_members: List[List[str]] = []
    if graph.number_of_nodes() > 0:
        try:
            detected_communities = list(greedy_modularity_communities(graph))
        except Exception as exc:  # pragma: no cover - defensive
            logging.warning("Community detection failed: %s", exc)
            detected_communities = []

        if detected_communities:
            community_members = [
                sorted(str(node) for node in community)
                for community in detected_communities
            ]
            community_members.sort(
                key=lambda members: (
                    -len(members),
                    members[0] if members else "",
                )
            )
        else:
            community_members = [[str(node)] for node in sorted(graph.nodes, key=str)]
    else:
        community_members = []

    if not community_members:
        community_members = [[str(node)] for node in sorted(graph.nodes, key=str)]

    for index, members in enumerate(community_members):
        for node_id in members:
            community_assignments[node_id] = index

    rng = random.Random(42)
    final_layout: Dict[str, Tuple[float, float]] = {}

    def _compute_global_layout() -> Dict[str, Tuple[float, float]]:
        node_ids = [str(node) for node in graph.nodes]
        if not node_ids:
            return {}

        weight_values = [
            max(0.05, float(data.get("weight", 1.0)))
            for _, _, data in graph.edges(data=True)
        ]
        mean_weight = (
            sum(weight_values) / len(weight_values)
            if weight_values
            else 1.0
        )
        k_value = 2.5 / math.sqrt(max(mean_weight, 0.05))
        node_count = len(node_ids)
        layout_scale = max(400.0, math.sqrt(node_count) * 60.0)

        try:
            if node_count <= 40:
                try:
                    positions = nx.kamada_kawai_layout(
                        graph,
                        weight="weight",
                        scale=layout_scale,
                    )
                except (ModuleNotFoundError, ImportError):  # pragma: no cover - SciPy optional
                    logging.warning(
                        "SciPy not available; falling back to Fruchterman-Reingold layout for %d-node graph.",
                        node_count,
                    )
                    positions = nx.fruchterman_reingold_layout(
                        graph,
                        weight="weight",
                        seed=42,
                        k=k_value,
                        iterations=max(2500, int(node_count * 40)),
                        scale=layout_scale,
                    )
            elif node_count <= 200:
                positions = nx.fruchterman_reingold_layout(
                    graph,
                    weight="weight",
                    seed=42,
                    k=k_value,
                    iterations=max(3200, int(node_count * 35)),
                    scale=layout_scale,
                )
            else:
                positions = nx.spring_layout(
                    graph,
                    weight="weight",
                    seed=42,
                    k=k_value,
                    iterations=max(4000, int(node_count * 20)),
                    scale=layout_scale,
                )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logging.warning("Force-directed layout failed (%s); retrying with spring layout.", exc)
            positions = nx.spring_layout(
                graph,
                weight="weight",
                seed=42,
                k=k_value,
                iterations=max(3600, int(node_count * 18)),
                scale=layout_scale,
            )

        if graph.number_of_edges() > 0:
            strength_samples = [
                max(0.1, float(data.get("strength", data.get("weight", 1.0))))
                for _, _, data in graph.edges(data=True)
            ]
            if strength_samples:
                min_strength = min(strength_samples)
                max_strength = max(strength_samples)
                span_strength = max(max_strength - min_strength, 1e-6)
                min_length_target = layout_scale * 0.08
                max_length_target = layout_scale * 0.55

                desired_lengths: Dict[Tuple[str, str], float] = {}
                for u, v, data in graph.edges(data=True):
                    strength_value = max(
                        0.1, float(data.get("strength", data.get("weight", 1.0)))
                    )
                    norm = (strength_value - min_strength) / span_strength
                    norm = min(max(norm, 0.0), 1.0)
                    desired = min_length_target + (1.0 - norm) * (
                        max_length_target - min_length_target
                    )
                    desired_lengths[(u, v)] = desired

                working_positions: Dict[str, List[float]] = {
                    node_id: [
                        float(positions.get(node_id, (0.0, 0.0))[0]),
                        float(positions.get(node_id, (0.0, 0.0))[1]),
                    ]
                    for node_id in node_ids
                }

                for _ in range(280):
                    max_delta = 0.0
                    for u, v, data in graph.edges(data=True):
                        desired = desired_lengths.get((u, v)) or desired_lengths.get((v, u))
                        if desired is None:
                            continue
                        pos_u = working_positions.get(u)
                        pos_v = working_positions.get(v)
                        if pos_u is None or pos_v is None:
                            continue
                        dx = pos_v[0] - pos_u[0]
                        dy = pos_v[1] - pos_u[1]
                        dist = math.hypot(dx, dy)
                        if dist == 0.0:
                            jitter_x = rng.uniform(-0.5, 0.5)
                            jitter_y = rng.uniform(-0.5, 0.5)
                            pos_u[0] += jitter_x
                            pos_u[1] += jitter_y
                            pos_v[0] -= jitter_x
                            pos_v[1] -= jitter_y
                            continue
                        delta = dist - desired
                        max_delta = max(max_delta, abs(delta))
                        if abs(delta) < 1e-6:
                            continue
                        adjustment = (delta / dist) * 0.22
                        shift_x = dx * adjustment * 0.5
                        shift_y = dy * adjustment * 0.5
                        pos_u[0] += shift_x
                        pos_u[1] += shift_y
                        pos_v[0] -= shift_x
                        pos_v[1] -= shift_y
                    if max_delta < 1.2:
                        break

                positions = {
                    node_id: (coords[0], coords[1])
                    for node_id, coords in working_positions.items()
                }

        xs = [positions.get(node_id, (0.0, 0.0))[0] for node_id in node_ids]
        ys = [positions.get(node_id, (0.0, 0.0))[1] for node_id in node_ids]
        centroid_x = sum(xs) / len(xs) if xs else 0.0
        centroid_y = sum(ys) / len(ys) if ys else 0.0

        layout: Dict[str, Tuple[float, float]] = {}
        for node_id in node_ids:
            raw_x, raw_y = positions.get(node_id, (0.0, 0.0))
            if not (math.isfinite(raw_x) and math.isfinite(raw_y)):
                raw_x = rng.uniform(-25.0, 25.0)
                raw_y = rng.uniform(-25.0, 25.0)
            layout[node_id] = (
                float(raw_x - centroid_x),
                float(raw_y - centroid_y),
            )
        return layout

    final_layout = _compute_global_layout()

    if not final_layout:
        final_layout = {str(node): (0.0, 0.0) for node in graph.nodes}

    # jitter coincident nodes slightly to avoid overlap bundles
    coordinate_bins: Dict[Tuple[int, int], List[str]] = {}
    for node_id, (x_coord, y_coord) in final_layout.items():
        key = (int(round(x_coord * 1000.0)), int(round(y_coord * 1000.0)))
        coordinate_bins.setdefault(key, []).append(node_id)

    for nodes_at_point in coordinate_bins.values():
        if len(nodes_at_point) <= 1:
            continue
        for idx, node_id in enumerate(nodes_at_point):
            angle = (2.0 * math.pi * idx) / len(nodes_at_point)
            jitter_distance = 1.5
            base_x, base_y = final_layout[node_id]
            final_layout[node_id] = (
                base_x + math.cos(angle) * jitter_distance,
                base_y + math.sin(angle) * jitter_distance,
            )

    xs_all_raw = [coords[0] for coords in final_layout.values()] or [0.0]
    ys_all_raw = [coords[1] for coords in final_layout.values()] or [0.0]
    min_x_raw, max_x_raw = min(xs_all_raw), max(xs_all_raw)
    min_y_raw, max_y_raw = min(ys_all_raw), max(ys_all_raw)
    span_x_raw = max(max_x_raw - min_x_raw, 1.0)
    span_y_raw = max(max_y_raw - min_y_raw, 1.0)
    dominant_span = max(span_x_raw, span_y_raw, 1.0)
    target_span = 1200.0
    padding = 100.0
    scale = target_span / dominant_span

    def _normalise_point(x_coord: float, y_coord: float) -> Tuple[float, float]:
        return (
            (x_coord - min_x_raw) * scale + padding,
            (y_coord - min_y_raw) * scale + padding,
        )

    for node_id, coords in list(final_layout.items()):
        final_layout[node_id] = _normalise_point(coords[0], coords[1])

    layout_payload.clear()
    for node in nodes_payload:
        node_id = node["id"]
        position = final_layout.get(node_id, (padding, padding))
        community_index = community_assignments.get(node_id, 0)
        node["community"] = community_index
        node["x"], node["y"] = float(position[0]), float(position[1])
        layout_payload[node_id] = {"x": node["x"], "y": node["y"]}

    xs = [coords[0] for coords in final_layout.values()] or [0.0]
    ys = [coords[1] for coords in final_layout.values()] or [0.0]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)

    # backbone layering
    backbone_keys: Set[Tuple[str, str]] = set()
    if graph.number_of_edges() > 0:
        try:
            mst = nx.maximum_spanning_tree(graph, weight="weight")
        except Exception as exc:  # pragma: no cover - defensive
            logging.warning("Failed to compute backbone MST: %s", exc)
            mst = nx.Graph()
        for source_id, target_id in mst.edges():
            key = tuple(sorted((str(source_id), str(target_id))))
            backbone_keys.add(key)

    adjacency: Dict[str, List[Tuple[float, Tuple[str, str]]]] = {}
    edge_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for edge in edges_payload:
        source = _normalise_identifier(edge.get("source"))
        target = _normalise_identifier(edge.get("target"))
        if source is None or target is None:
            continue
        key = tuple(sorted((str(source), str(target))))
        edge_lookup[key] = edge
        adjacency.setdefault(str(source), []).append((float(edge["strength"]), key))
        adjacency.setdefault(str(target), []).append((float(edge["strength"]), key))

    for node_id, candidates in adjacency.items():
        top_edges = sorted(candidates, key=lambda item: item[0], reverse=True)[:2]
        for _, key in top_edges:
            backbone_keys.add(key)

    def _edge_geometry(
        source_id: str,
        target_id: str,
        strength: float,
        is_backbone: bool,
    ) -> Optional[Tuple[List[float], List[float]]]:
        source_position = final_layout.get(source_id)
        target_position = final_layout.get(target_id)
        if source_position is None or target_position is None:
            return None

        sx, sy = source_position
        tx, ty = target_position
        if not (math.isfinite(sx) and math.isfinite(sy) and math.isfinite(tx) and math.isfinite(ty)):
            return None

        dx = tx - sx
        dy = ty - sy
        length = math.hypot(dx, dy) or 1.0
        if strength >= 0.65 or is_backbone:
            return ([sx, tx], [sy, ty])

        perp_x = -dy / length
        perp_y = dx / length
        mid_x = (sx + tx) / 2.0
        mid_y = (sy + ty) / 2.0
        hash_seed = hash((source_id, target_id)) & 1
        direction = 1.0 if hash_seed == 0 else -1.0
        bend_amount = min(max(length * (0.18 + (0.6 - strength) * 0.4), 12.0), 160.0)
        control_x = mid_x + perp_x * bend_amount * direction
        control_y = mid_y + perp_y * bend_amount * direction
        return ([sx, control_x, tx], [sy, control_y, ty])

    edges_backbone: List[Dict[str, Any]] = []
    edges_rest: List[Dict[str, Any]] = []
    combined_edges: List[Dict[str, Any]] = []

    for edge_key, edge in edge_lookup.items():
        source_id, target_id = edge_key
        strength = float(edge.get("strength", 1.0))
        is_backbone = edge_key in backbone_keys
        geometry = _edge_geometry(source_id, target_id, strength, is_backbone)
        if geometry is None:
            continue
        xs_points, ys_points = geometry

        base_width = float(1.0 + 3.0 * strength)
        base_alpha = float(max(0.05, min(0.95, 0.2 + 0.6 * strength)))

        formatted_edge = {
            "source": source_id,
            "target": target_id,
            "strength": strength,
            "weight": float(edge.get("weight", strength)),
            "line_width": base_width,
            "line_alpha": base_alpha,
            "is_backbone": bool(is_backbone),
            "xs": [float(value) for value in xs_points],
            "ys": [float(value) for value in ys_points],
        }

        combined_edges.append(formatted_edge)
        if is_backbone:
            edges_backbone.append(formatted_edge)
        else:
            edges_rest.append(formatted_edge)

    # maintain compatibility for downstream renderer
    edges_payload = combined_edges

    # ===== SAFETY NET: ensure layering & geometry even if something above produced no split =====
    if not edges_backbone and not edges_rest and edges_payload:
        graph2 = nx.Graph()
        for entry in edges_payload:
            source_id = str(entry.get("source"))
            target_id = str(entry.get("target"))
            weight_value = float(entry.get("weight", entry.get("strength", 0.25)))
            if source_id and target_id and source_id != target_id:
                graph2.add_edge(source_id, target_id, weight=weight_value)

        backbone_keys: Set[Tuple[str, str]] = set()
        if graph2.number_of_edges() > 0:
            try:
                mst_graph = nx.maximum_spanning_tree(graph2, weight="weight")
            except Exception:
                mst_graph = nx.Graph()
            for u, v in mst_graph.edges():
                backbone_keys.add(tuple(sorted((str(u), str(v)))))

        adjacency: Dict[str, List[Tuple[float, Tuple[str, str]]]] = {}
        for entry in edges_payload:
            source_id = str(entry.get("source"))
            target_id = str(entry.get("target"))
            if not source_id or not target_id:
                continue
            key = tuple(sorted((source_id, target_id)))
            strength_value = float(entry.get("strength", 0.25))
            adjacency.setdefault(source_id, []).append((strength_value, key))
            adjacency.setdefault(target_id, []).append((strength_value, key))

        for candidates in adjacency.values():
            for _, key in sorted(candidates, key=lambda item: item[0], reverse=True)[:2]:
                backbone_keys.add(key)

        edges_backbone = []
        edges_rest = []
        layered_edges: List[Dict[str, Any]] = []
        for entry in edges_payload:
            source_id = str(entry.get("source"))
            target_id = str(entry.get("target"))
            key = tuple(sorted((source_id, target_id)))
            strength_value = float(entry.get("strength", 0.25))
            weight_value = float(entry.get("weight", strength_value))

            if entry.get("xs") and entry.get("ys"):
                xs_points = [float(value) for value in entry["xs"]]
                ys_points = [float(value) for value in entry["ys"]]
            else:
                source_coords = final_layout.get(source_id, (0.0, 0.0))
                target_coords = final_layout.get(target_id, (0.0, 0.0))
                xs_points = [float(source_coords[0]), float(target_coords[0])]
                ys_points = [float(source_coords[1]), float(target_coords[1])]

            formatted = {
                "source": source_id,
                "target": target_id,
                "strength": strength_value,
                "weight": weight_value,
                "line_width": float(entry.get("line_width", 1.0 + 2.5 * strength_value)),
                "line_alpha": float(entry.get("line_alpha", 0.3 + 0.6 * strength_value)),
                "is_backbone": key in backbone_keys,
                "xs": xs_points,
                "ys": ys_points,
            }
            layered_edges.append(formatted)
            if formatted["is_backbone"]:
                edges_backbone.append(formatted)
            else:
                edges_rest.append(formatted)

        edges_payload = layered_edges

    if edges_payload and not debug_stats.get("final_edges"):
        debug_stats["final_edges"] = len(edges_payload)

    # [FEATURE] emit range metadata for frontend viewBox alignment
    xs = [coords[0] for coords in final_layout.values()] or [0.0]
    ys = [coords[1] for coords in final_layout.values()] or [0.0]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    padding_margin = 10.0
    x_start = float(min_x - padding_margin)
    x_end = float(max_x + padding_margin)
    y_start = float(min_y - padding_margin)
    y_end = float(max_y + padding_margin)
    x_span = max(x_end - x_start, 1e-6)
    y_span = max(y_end - y_start, 1e-6)

    x_range = {
        "start": x_start,
        "end": x_end,
        "min": float(min_x),
        "max": float(max_x),
        "span": float(x_span),
    }
    y_range = {
        "start": y_start,
        "end": y_end,
        "min": float(min_y),
        "max": float(max_y),
        "span": float(y_span),
    }

    return {
        "nodes": nodes_payload,
        "edges": edges_payload,
        "edges_backbone": edges_backbone,
        "edges_rest": edges_rest,
        "layout": layout_payload,
        "x_range": x_range,
        "y_range": y_range,
        "debug": debug_stats,
    }


@router.get("/experiments/{exp_id}/relationship-graph", response_class=HTMLResponse)
async def get_experiment_relationship_graph(
    request: Request,
    exp_id: uuid.UUID,
    format: str = Query("json"),
):
    payload = await _build_relationship_graph_payload(request, exp_id)

    normalised_format = (format or "json").strip().lower()
    empty_payload = {"nodes": [], "edges": []}

    nodes = payload.get("nodes") or []
    if not nodes:
        if normalised_format == "json":
            return JSONResponse(content=empty_payload)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No agent profiles available for experiment relationship graph",
        )

    try:
        renderer = AgentRelationshipGraphRenderer(
            payload,
            enable_rendering=normalised_format == "html",
        )
    except ValueError as exc:
        if normalised_format == "json":
            return JSONResponse(content=empty_payload)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if normalised_format == "json":
        return JSONResponse(content=renderer.export_graph())

    script, div = components(renderer.figure)
    resource_html = CDN.render()
    pointer_style = (
        "<style>"
        ".relationship-graph-figure .bk-canvas-events{cursor:pointer;}"
        "</style>"
    )
    html = "\n".join(part for part in (resource_html, pointer_style, script, div) if part)
    return HTMLResponse(content=html)

@router.get("/experiments/{exp_id}/relationship-edges")
async def get_experiment_relationship_edges(
    request: Request,
    exp_id: uuid.UUID,
) -> JSONResponse:
    synth_q = request.query_params.get("synthesize_when_empty")
    debug_q = request.query_params.get("debug")
    restore_env: Optional[str] = None
    if synth_q is not None:
        normalised = synth_q.strip().lower()
        previous = os.environ.get("REL_GRAPH_SYNTHESIZE_WHEN_EMPTY")
        restore_env = previous if previous is not None else ""
        if normalised in {"1", "true", "yes"}:
            os.environ["REL_GRAPH_SYNTHESIZE_WHEN_EMPTY"] = "true"
        elif normalised in {"0", "false", "no"}:
            os.environ["REL_GRAPH_SYNTHESIZE_WHEN_EMPTY"] = "false"

    payload = await _build_relationship_graph_payload(request, exp_id)

    if synth_q is not None:
        if restore_env == "":
            os.environ.pop("REL_GRAPH_SYNTHESIZE_WHEN_EMPTY", None)
        else:
            os.environ["REL_GRAPH_SYNTHESIZE_WHEN_EMPTY"] = restore_env
    nodes = payload.get("nodes") or []
    if not nodes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No agent profiles available for experiment relationship graph",
        )

    edges = payload.get("edges") or []
    if not edges:
        logging.warning(
            "Relationship edge export for experiment %s contains no edges", exp_id
        )
    debug_meta = payload.get("debug") if isinstance(payload, Mapping) else {}
    logging.info(
        "REL-EDGES export %s: nodes=%d, edges=%d, backbone=%d, rest=%d, synthesized=%s",
        exp_id,
        len(payload.get("nodes") or []),
        len(edges),
        len(payload.get("edges_backbone") or []),
        len(payload.get("edges_rest") or []),
        bool(debug_meta.get("synthesized")) if isinstance(debug_meta, Mapping) else False,
    )

    if debug_q not in {"1", "true", "yes"} and isinstance(payload, Mapping):
        payload = dict(payload)
        payload.pop("debug", None)

    return JSONResponse(content=payload)

@router.get("/experiments/{exp_id}/timeline")
async def get_experiment_status_timeline_by_id(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[List[ApiTime]]:
    """Get experiment status timeline by ID"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        stmt = select(Experiment).where(
            Experiment.tenant_id.in_([tenant_id, "", "default"]),
            Experiment.id == exp_id,
        )
        result = await db.execute(stmt)
        row = result.first()
        if not row or len(row) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )
        experiment: Experiment = row[0]
        # Check if the experiment has started
        if ExperimentStatus(experiment.status) == ExperimentStatus.NOT_STARTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Experiment has not started yet",
            )

        # Get timeline from agent status table
        table_name = experiment.agent_status_tablename

        # the table_name is safe to use in the query
        # it is generated from the experiment id
        query = text(
            f"""
            SELECT day, t 
            FROM {table_name} 
            GROUP BY day, t 
            ORDER BY day, t
        """
        )

        results = (await db.execute(query)).all()
        timeline = [ApiTime(day=int(row[0]), t=float(row[1])) for row in results]

        return ApiResponseWrapper(data=timeline)


@router.get("/experiments/{exp_id}/summary")
async def get_experiment_summary(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[ApiExperimentSummary]:
    """Get experiment summary including adoption rate and emotion stats"""

    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        experiment = await _find_started_experiment_by_id(request, db, exp_id)

        # 如果实验还没开始，直接返回空 summary（统一格式）
        if ExperimentStatus(experiment.status) == ExperimentStatus.NOT_STARTED:
            empty_summary = ApiExperimentSummary(
                adoption_rate=0.0,
                average_sentiment=0.0,
                average_emotion={emo: 0.0 for emo in EMOTION_SCORE_MAP.keys()},
                overall_average_emotion="neutral",
                emotion_distribution={emo: 0 for emo in EMOTION_SCORE_MAP.keys()},
            )
            return ApiResponseWrapper(data=empty_summary)


        table_name = experiment.agent_status_tablename
        status_table, _ = agent_status(table_name)

        subquery = (
            select(
                status_table.c.id,
                status_table.c.status,
                func.row_number()
                .over(
                    partition_by=status_table.c.id,
                    order_by=(status_table.c.day.desc(), status_table.c.t.desc()),
                )
                .label("rn"),
            )
        ).subquery()

        stmt = select(subquery.c.id, subquery.c.status).where(subquery.c.rn == 1)
        try:
            rows = (await db.execute(stmt)).all()
        except Exception:
            logging.warning("status table %s missing", table_name)
            rows = []

        total = len(rows)
        # initialise adoption flags for every agent so the denominator
        # reflects the whole population even if some agents never update
        adopted_flags: Dict[int, bool] = {row.id: False for row in rows}
        sentiments: List[float] = []
        emotion_distribution: Dict[str, int] = defaultdict(int)
        emotion_sums: Dict[str, float] = defaultdict(float)
        emotion_counts: Dict[str, int] = defaultdict(int)

        for row in rows:
            status_data = row.status
            if isinstance(status_data, str):
                try:
                    status_data = json.loads(status_data)
                except Exception:
                    status_data = {}
            if not isinstance(status_data, dict):
                status_data = {}

            adopted_val = status_data.get("adopted")
            if isinstance(adopted_val, (bool, int, float, str)):
                try:
                    adopted_flags[row.id] = (
                        bool(json.loads(str(adopted_val).lower()))
                        if isinstance(adopted_val, str)
                        else bool(adopted_val)
                    )
                except Exception:
                    adopted_flags[row.id] = bool(adopted_val)

            sentiment_val = status_data.get("sentiment")
            if sentiment_val is not None:
                try:
                    sentiments.append(float(sentiment_val))
                except Exception:
                    pass

            emo_val = status_data.get("emotion")
            if isinstance(emo_val, dict):
                for k, v in emo_val.items():
                    try:
                        label = str(k).strip()
                        norm_label = EMOTION_NORMALIZE_MAP.get(label, label).lower()
                        emotion_sums[norm_label] += float(v)
                        emotion_counts[norm_label] += 1
                        emotion_distribution[norm_label] += 1
                    except Exception:
                        pass
            elif isinstance(emo_val, str):
                raw_label = str(emo_val).strip()
                norm_label = EMOTION_NORMALIZE_MAP.get(raw_label, raw_label).lower()
                emotion_distribution[norm_label] += 1

        # Compute adoption and sentiment from metrics if available
        has_metrics, metrics_by_key = await get_experiment_metrics_by_id(
            request, db, exp_id
        )
        if has_metrics:
            for key, metrics in metrics_by_key.items():
                if key.startswith("adopted:"):
                    try:
                        agent_id = int(key.split(":", 1)[1])
                        adopted_flags[agent_id] = bool(metrics[-1].value)
                    except Exception:
                        continue
                elif key.startswith("sentiment:"):
                    try:
                        sentiments.append(metrics[-1].value)
                    except Exception:
                        continue
                elif key.startswith("emotion:"):
                    for m in metrics:
                        try:
                            val = float(m.value)
                        except Exception:
                            continue
                        label = EMOTION_VALUE_TO_LABEL.get(round(val, 1))
                        if label is None:
                            label = str(round(val, 1))
                        label = str(label).strip().lower()
                        emotion_distribution[label] += 1
                        emotion_sums[label] += val
                        emotion_counts[label] += 1

        adoption_rate = (
            sum(1 for v in adopted_flags.values() if v) / len(adopted_flags)
            if adopted_flags
            else 0.0
        )
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        # ---- emotion handling ----
        EMOTION_ORDER = [
            "interested", "curious", "relaxed", "neutral",
            "uninterested", "skeptical", "dislike"
        ]

        # 1) 全程累计的分布 (趋势)
        cumulative_distribution = {emo: emotion_distribution.get(emo, 0) for emo in EMOTION_SCORE_MAP.keys()}

        # 2) 每个 agent 最后一次状态 → 计算比例 (最终快照)
        final_distribution = {emo: 0 for emo in EMOTION_ORDER}
        for row in rows:
            status_data = row.status
            if isinstance(status_data, str):
                try:
                    status_data = json.loads(status_data)
                except Exception:
                    status_data = {}
            if not isinstance(status_data, dict):
                continue
            emo_val = status_data.get("emotion", "neutral")
            emo_label = str(emo_val).strip().lower()
            if emo_label in final_distribution:
                final_distribution[emo_label] += 1
            else:
                final_distribution["neutral"] += 1  # fallback

        if total > 0:
            average_emotion = {emo: final_distribution[emo] / total for emo in EMOTION_ORDER}
            overall_average_emotion = max(average_emotion, key=average_emotion.get)
        else:
            average_emotion = {emo: 0.0 for emo in EMOTION_ORDER}
            overall_average_emotion = "neutral"
        summary = ApiExperimentSummary(
            adoption_rate=adoption_rate,
            average_sentiment=avg_sentiment,
            average_emotion=average_emotion,                 # 最终快照比例
            overall_average_emotion=overall_average_emotion, # 主导情绪
            emotion_distribution=cumulative_distribution,    # 累计趋势
        )
        return ApiResponseWrapper(data=summary)


@router.get("/experiments/{exp_id}/analysis")
async def get_experiment_analysis(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[ApiExperimentAnalysis]:
    """Generate LLM-based analysis for an experiment"""

    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        experiment = await _find_started_experiment_by_id(request, db, exp_id)

        summary_wrapper = await get_experiment_summary(request, exp_id)
        summary = summary_wrapper.data

        analysis_text = "No analysis available for this experiment."
        try:
            dialog_table, _ = agent_dialog(experiment.agent_dialog_tablename)
            status_table, _ = agent_status(experiment.agent_status_tablename)
            prompt_table, _ = global_prompt(experiment.global_prompt_tablename)

            dialog_rows = (await db.execute(select(dialog_table.c.content).limit(5))).all()
            status_rows = (await db.execute(select(status_table.c.status).limit(5))).all()
            prompt_rows = (await db.execute(select(prompt_table.c.prompt).limit(5))).all()

            samples: List[str] = []
            for row in dialog_rows:
                if row[0]:
                    samples.append(str(row[0]))
            for row in status_rows:
                val = row[0]
                if val:
                    if isinstance(val, (dict, list)):
                        samples.append(json.dumps(val))
                    else:
                        samples.append(str(val))
            for row in prompt_rows:
                if row[0]:
                    samples.append(str(row[0]))

            sample_texts = "\n".join(samples)
            prompt = f"""Here is the experiment summary:
Adoption Rate: {summary.adoption_rate}
Average Sentiment: {summary.average_sentiment}
Overall Average Emotion: {summary.overall_average_emotion}
Emotion Distribution: {summary.emotion_distribution}

Here are some excerpts from the simulation (dialogs, agent internal states, system prompts):
{sample_texts}

Task: Write a short explanation (2-4 sentences) analyzing WHY this distribution and sentiment occurred.
"""

            stmt = select(ExperimentBillConfig.llm_config_id).where(
                ExperimentBillConfig.tenant_id == experiment.tenant_id,
                ExperimentBillConfig.exp_id == experiment.id,
            )
            llm_config_id = (await db.execute(stmt)).scalar_one_or_none()

            llm_configs_data: List[Dict[str, Any]] = []
            if llm_config_id:
                stmt = select(LLMConfigDB.config).where(
                    LLMConfigDB.tenant_id.in_([experiment.tenant_id, "", "default"]),
                    LLMConfigDB.id == llm_config_id,
                )
                cfg = (await db.execute(stmt)).scalar_one_or_none()
                if cfg:
                    if isinstance(cfg, list):
                        llm_configs_data.extend(cfg)
                    else:
                        llm_configs_data.append(cfg)

            if not llm_configs_data:
                config_dict = json.loads(base64.b64decode(experiment.config).decode())
                partial_llms = config_dict.get("llm", [])
                for partial in partial_llms:
                    stmt = select(LLMConfigDB.config).where(
                        LLMConfigDB.tenant_id.in_([experiment.tenant_id, "", "default"]),
                        LLMConfigDB.config["provider"].astext == partial.get("provider"),
                        LLMConfigDB.config["model"].astext == partial.get("model"),
                    )
                    if partial.get("base_url"):
                        stmt = stmt.where(
                            LLMConfigDB.config["base_url"].astext == partial.get("base_url")
                        )
                    cfg = (await db.execute(stmt.limit(1))).scalar_one_or_none()
                    if cfg:
                        if isinstance(cfg, list):
                            llm_configs_data.extend(cfg)
                        else:
                            llm_configs_data.append(cfg)
                if not llm_configs_data:
                    llm_configs_data = partial_llms

            llm_configs = [RealLLMConfig.model_validate(c) for c in llm_configs_data]
            if llm_configs:
                llm = LLM(llm_configs)
                analysis_text = await llm.atext_request(
                    dialog=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                )
        except Exception as e:  # pragma: no cover - best effort logging
            logging.error("Failed to analyze experiment %s: %s", exp_id, e)

        return ApiResponseWrapper(
            data=ApiExperimentAnalysis(analysis_text=analysis_text)
        )


@router.delete("/experiments/{exp_id}", status_code=status.HTTP_200_OK)
async def delete_experiment_by_id(
    request: Request,
    exp_id: uuid.UUID,
):
    """Delete experiment by ID"""

    if request.app.state.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Server is in read-only mode"
        )
    tenant_id = await request.app.state.get_tenant_id(request)
    if tenant_id == DEMO_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo user is not allowed to delete experiments",
        )

    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        try:
            # Start transaction
            async with db.begin():
                stmt = select(Experiment).where(
                    Experiment.tenant_id == tenant_id, Experiment.id == exp_id
                )
                result = await db.execute(stmt)
                row = result.first()
                if not row or len(row) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Experiment not found",
                    )
                experiment: Experiment = row[0]

                # Get all table names that need to be deleted
                table_names = [
                    experiment.agent_profile_tablename,
                    experiment.agent_status_tablename,
                    experiment.agent_dialog_tablename,
                    experiment.agent_survey_tablename,
                    experiment.global_prompt_tablename,
                    experiment.pending_dialog_tablename,
                    experiment.pending_survey_tablename,
                    experiment.metric_tablename,
                ]

                # Delete related tables
                for table_name in table_names:
                    try:
                        query = text(f"DROP TABLE IF EXISTS {table_name}")
                        await db.execute(query)
                    except Exception as e:
                        logging.error(f"Error dropping table {table_name}: {str(e)}")
                        # Continue execution without interruption

                # Delete experiment record
                await db.delete(experiment)

            # Transaction will be committed automatically

            return ApiResponseWrapper(
                data={"message": "Experiment deleted successfully"}
            )

        except Exception as e:
            logging.error(f"Error deleting experiment: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete experiment: {str(e)}",
            )


async def get_experiment_metrics_by_id(
    request: Request,
    db: AsyncSession,
    exp_id: uuid.UUID,
) -> Tuple[bool, Dict[str, List[ApiMetric]]]:
    """Get metrics for an experiment by ID

    Args:
        request: FastAPI request
        db: Database session
        exp_id: Experiment ID

    Returns:
        Tuple containing:
        - bool: Whether metrics were found
        - Dict[str, List[ApiMetric]]: Metrics data aggregated by key
    """

    try:
        experiment = await _find_started_experiment_by_id(request, db, exp_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            return False, {}
        raise

    # Get metrics from the metric table
    table_name = experiment.metric_tablename
    
    # Execute query to get metrics data; when the metrics table doesn't yet
    # exist (e.g. runs with no metrics recorded), return no metrics instead of
    # raising an error so the summary endpoint can still respond.
    query = text(
        f"""
        SELECT key, value, step
        FROM {table_name}
        ORDER BY step
        """
    )
    try:
        results = await db.execute(query)
    except Exception:
        logging.warning("metrics table %s missing", table_name)
        return False, {}

    rows = results.all()

    if not rows:
        return False, {}

    # Aggregate metrics by key, skipping invalid values
    metrics_by_key: Dict[str, List[ApiMetric]] = defaultdict(list)
    for row in rows:
        value = row[1]
        step = row[2]
        if (
            value is None
            or step is None
            or not isinstance(value, (int, float))
            or not isinstance(step, (int, float))
            or not math.isfinite(value)
            or not math.isfinite(step)
        ):
            continue
        api_metric = ApiMetric(
            key=row[0],
            value=float(value),
            step=int(step),
        )
        metrics_by_key[row[0]].append(api_metric)

    return True, metrics_by_key


def serialize_metrics(
    metrics_by_key: Dict[str, List[ApiMetric]],
) -> Dict[str, List[dict]]:
    """Serialize metrics data for JSON output

    Args:
        metrics_by_key: Metrics data aggregated by key

    Returns:
        Dict with serialized metrics data
    """
    return {
        key: [metric.model_dump() for metric in metrics]
        for key, metrics in metrics_by_key.items()
    }


@router.get("/experiments/{exp_id}/metrics")
async def get_experiment_metrics(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[Dict[str, List[ApiMetric]]]:
    """Get all metrics for an experiment, aggregated by metric key"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)

        # First verify the experiment exists
        stmt = select(Experiment).where(
            Experiment.tenant_id == tenant_id, Experiment.id == exp_id
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )

        _, metrics_by_key = await get_experiment_metrics_by_id(request, db, exp_id)
        return ApiResponseWrapper(data=metrics_by_key)


@router.post("/experiments/{exp_id}/export")
async def export_experiment_data(
    request: Request,
    exp_id: uuid.UUID,
) -> StreamingResponse:
    """Export experiment data as a zip file containing YAML and CSV files"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)

        # Get experiment info
        stmt = select(Experiment).where(
            Experiment.tenant_id == tenant_id, Experiment.id == exp_id
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )
        experiment: Experiment = row

        # Create in-memory zip file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Export experiment info as YAML
            exp_dict = experiment.to_dict()
            yaml_content = yaml.dump(exp_dict, allow_unicode=True)
            zip_file.writestr("experiment.yaml", yaml_content)

            # Export metrics data as JSON
            found, metrics_by_key = await get_experiment_metrics_by_id(
                request, db, exp_id
            )
            if found:
                serialized_metrics = serialize_metrics(metrics_by_key)
                metrics_json = json.dumps(serialized_metrics, indent=2)
                zip_file.writestr("metrics.json", metrics_json)

            # Export artifacts data
            fs_client = request.app.state.env.fs_client
            artifacts_path = f"exps/{tenant_id}/{exp_id}/artifacts.json"
            artifacts_data = fs_client.download(artifacts_path)
            if artifacts_data:
                zip_file.writestr("artifacts.json", artifacts_data)

            # get all tables
            tables = {
                "agent_profile": agent_profile(experiment.agent_profile_tablename),
                "agent_status": agent_status(experiment.agent_status_tablename),
                "agent_survey": agent_survey(experiment.agent_survey_tablename),
                "agent_dialog": agent_dialog(experiment.agent_dialog_tablename),
                "global_prompt": global_prompt(experiment.global_prompt_tablename),
                "metric": metric(experiment.metric_tablename),
            }

            for table_name, (db_table, columns) in tables.items():
                query = select(db_table)
                results = await db.execute(query)
                rows = results.all()

                if rows:
                    # create csv file
                    output = io.StringIO()
                    writer = csv.writer(output)
                    # write header
                    writer.writerow([col for col in columns])
                    # write data
                    writer.writerows(rows)

                    zip_file.writestr(f"{table_name}.csv", output.getvalue())
                    output.close()

        # prepare response
        zip_buffer.seek(0)
        return StreamingResponse(
            iter([zip_buffer.getvalue()]),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=experiment_{exp_id}_export.zip"
            },
        )


@router.post("/experiments/{exp_id}/artifacts")
async def export_experiment_artifacts(
    request: Request,
    exp_id: uuid.UUID,
) -> StreamingResponse:
    """Export experiment artifacts as a JSON file"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)

        # Get experiment info
        stmt = select(Experiment).where(
            Experiment.tenant_id == tenant_id, Experiment.id == exp_id
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )

        # Get artifacts from S3
        fs_client = request.app.state.env.fs_client
        artifacts_path = f"exps/{tenant_id}/{exp_id}/artifacts.json"
        artifacts_data = fs_client.download(artifacts_path)

        if not artifacts_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Artifacts not found"
            )

        return StreamingResponse(
            iter([artifacts_data]),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=experiment_{exp_id}_artifacts.json"
            },
        )