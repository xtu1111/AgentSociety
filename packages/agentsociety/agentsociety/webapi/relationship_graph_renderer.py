"""Interactive agent relationship graph renderer."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import uuid
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
)

import networkx as nx
from networkx.algorithms import community as nx_community
from bokeh.document import Document
from bokeh.io import curdoc
from bokeh.models import ColumnDataSource, HoverTool, Range1d, TapTool, CustomJS
from bokeh.plotting import figure

if TYPE_CHECKING:  # pragma: no cover
    from bokeh.plotting import Figure

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EdgeKey:
    source: str
    target: str

    def __post_init__(self) -> None:  # pragma: no cover
        object.__setattr__(self, "source", str(self.source).strip())
        object.__setattr__(self, "target", str(self.target).strip())

    def as_tuple(self) -> Tuple[str, str]:
        a, b = sorted((self.source, self.target))
        return a, b


class AgentRelationshipGraphRenderer:
    """Render an interactive relationship graph using backend supplied geometry."""

    _RELATIONSHIP_CONTAINER_KEYS: Tuple[str, ...] = (
        "edges",
        "relationships",
        "connections",
        "links",
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
    )

    _RELATIONSHIP_KEY_SUBSTRINGS: Tuple[str, ...] = (
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
    )

    def __init__(
        self,
        graph_data: Mapping[str, Any] | str,
        *,
        doc: Optional[Document] = None,
        width: int = 820,
        height: int = 620,
        highlight_color: str = "#ff7f0e",
        highlight_width_delta: float = 1.1,
        flash_count: int = 3,
        flash_interval: float = 0.25,
        enable_rendering: bool = False,
        layout_algorithm: Optional[str] = None,
        hide_weak_edges: bool | float = False,
        **_ignored_layout_params: Any,
    ) -> None:
        self._document: Document = doc or curdoc()
        self._graph = nx.Graph()
        self._edge_lookup: Dict[Tuple[str, str], int] = {}
        self._edge_tokens: Dict[Tuple[str, str], str] = {}
        self._flash_count = max(1, flash_count)
        self._flash_interval = max(0.05, flash_interval)
        self._enable_rendering = enable_rendering
        self._highlight_color = highlight_color
        self._highlight_width_delta = highlight_width_delta if highlight_width_delta > 0 else 1.1

        # [FEATURE] allow callers to supply a numeric hide-weak-edges threshold while
        # remaining backward compatible with the historical boolean flag.
        threshold: Optional[float]
        if isinstance(hide_weak_edges, bool):
            threshold = 0.05 if hide_weak_edges else None
        else:
            try:
                parsed = float(hide_weak_edges)
                threshold = max(0.0, min(parsed, 1.0))
            except (TypeError, ValueError):
                threshold = None
        self._hide_weak_edges_threshold = threshold
        self._hide_weak_edges_enabled = threshold is not None

        allowed_algorithms = {"spring", "kamada_kawai", "fruchterman"}
        self._layout_algorithm_choice: Optional[str] = None
        if layout_algorithm:
            candidate = layout_algorithm.strip().lower()
            if candidate in allowed_algorithms:
                self._layout_algorithm_choice = candidate
            else:
                logger.warning(
                    "Unknown layout_algorithm '%s'; defaulting to automatic selection.",
                    layout_algorithm,
                )

        if _ignored_layout_params:
            logger.debug(
                "Renderer layout parameters are ignored; coordinates must come from the payload."
            )

        self._source_keys = ("source", "source_id", "from", "from_id", "agent", "agent_id", "id")
        self._target_keys = (
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
        self._relationship_key_set = {key.lower() for key in self._RELATIONSHIP_CONTAINER_KEYS}

        payload = self._load_graph_payload(graph_data)
        self._build_graph(payload)

        layout_payload = self._extract_layout(payload)
        node_ids = [str(node) for node in self._graph.nodes]
        provided_layout: Dict[str, Tuple[float, float]] = {}
        for node_id in node_ids:
            coords = layout_payload.get(node_id)
            if coords is None:
                continue
            try:
                provided_layout[node_id] = (float(coords[0]), float(coords[1]))
            except (TypeError, ValueError):
                logger.debug("Invalid coordinate payload for node %s; ignoring.", node_id)

        total_nodes = len(node_ids)
        provided_ratio = (len(provided_layout) / total_nodes) if total_nodes else 0.0

        if total_nodes and provided_ratio >= 0.8 and provided_layout:
            logger.debug(
                "Using provided layout for %d/%d nodes (coverage %.1f%%).",
                len(provided_layout),
                total_nodes,
                provided_ratio * 100.0,
            )
            if len(provided_layout) < total_nodes:
                computed = self._compute_force_directed_layout(initial_positions=provided_layout)
                for node_id, coords in provided_layout.items():
                    computed[node_id] = coords
                if len(computed) < total_nodes:
                    missing_ids = [node for node in node_ids if node not in computed]
                    computed.update(self._generate_fallback_layout(missing_ids))
                node_layout = computed
            else:
                node_layout = provided_layout
        else:
            if provided_layout and total_nodes:
                logger.info(
                    "Provided layout covers %.1f%% of nodes; recomputing force-directed layout.",
                    provided_ratio * 100.0,
                )
            node_layout = self._compute_force_directed_layout(
                initial_positions=provided_layout if provided_layout else None
            )
            if len(node_layout) < total_nodes:
                missing_ids = [node for node in node_ids if node not in node_layout]
                if missing_ids:
                    logger.warning(
                        "Force-directed layout missing %d nodes; applying fallback placement.",
                        len(missing_ids),
                    )
                    node_layout.update(self._generate_fallback_layout(missing_ids))

        self._layout = node_layout
        self._layout_scale = self._estimate_layout_scale(node_layout)
        # [FEATURE] prefer backend-provided range metadata when available so the
        # frontend layout aligns with precomputed coordinates.
        self._range_metadata = self._extract_or_compute_ranges(payload, node_layout)

        self.node_source = self._build_node_source(node_layout)
        self.edge_source, self._base_edge_styles = self._build_edge_source()
        self.figure: Optional[Figure] = None

        if self._enable_rendering:
            x_range, y_range = self._build_plot_ranges(self._range_metadata)
            self.figure = self._build_figure(width, height, x_range, y_range)
            self._attach_renderers()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def activate_edge(self, source: str, target: str) -> bool:
        key = _EdgeKey(source, target).as_tuple()
        index = self._edge_lookup.get(key)
        if index is None:
            logger.debug("Conversation event references unknown edge %s", key)
            return False

        token = uuid.uuid4().hex
        self._edge_tokens[key] = token

        total_steps = self._flash_count * 2
        for step in range(total_steps):
            active = step % 2 == 0
            timeout_ms = int(step * self._flash_interval * 1000)
            self._document.add_timeout_callback(
                self._make_flash_callback(index, key, token, active), timeout_ms
            )

        reset_timeout = int(total_steps * self._flash_interval * 1000)
        self._document.add_timeout_callback(
            self._make_flash_callback(index, key, token, False, final=True),
            reset_timeout,
        )
        return True

    def export_graph(self) -> Dict[str, Any]:
        nodes: List[Dict[str, Any]] = []
        layout: Dict[str, Dict[str, float]] = {}
        node_data = self.node_source.data
        for idx, node_id in enumerate(node_data.get("id", [])):
            payload: Dict[str, Any] = {
                "id": node_id,
                "x": float(node_data["x"][idx]),
                "y": float(node_data["y"][idx]),
                "color": node_data["fill_color"][idx],
                "border_color": node_data["line_color"][idx],
                "alpha": float(node_data["alpha"][idx]),
                "size": float(node_data["size"][idx]),
            }
            label_column = node_data.get("label")
            if label_column is not None and idx < len(label_column):
                payload["label"] = label_column[idx]
            graph_attributes = self._graph.nodes.get(node_id, {})
            for key, value in graph_attributes.items():
                if key in payload:
                    continue
                payload[key] = self._serialise_json(value)
            nodes.append(payload)
            layout[node_id] = {"x": float(payload["x"]), "y": float(payload["y"])}

        edges: List[Dict[str, Any]] = []
        edge_data = self.edge_source.data
        xs_column = edge_data.get("xs", [])
        ys_column = edge_data.get("ys", [])
        for idx, source in enumerate(edge_data.get("source", [])):
            target = edge_data["target"][idx]
            strength_value = float(edge_data["strength"][idx])
            payload = {
                "source": source,
                "target": target,
                "strength": strength_value,
                "line_width": float(edge_data["line_width"][idx]),
                "line_alpha": float(edge_data["line_alpha"][idx]),
            }
            weight_column = edge_data.get("weight")
            if weight_column is not None and idx < len(weight_column):
                payload["weight"] = float(weight_column[idx])
            else:
                payload["weight"] = strength_value
            backbone_column = edge_data.get("is_backbone")
            if backbone_column is not None and idx < len(backbone_column):
                payload["is_backbone"] = bool(backbone_column[idx])
            if idx < len(xs_column) and idx < len(ys_column):
                payload["xs"] = [float(v) for v in xs_column[idx]]
                payload["ys"] = [float(v) for v in ys_column[idx]]
            edges.append(payload)

        range_meta = getattr(self, "_range_metadata", {}) or {}
        x_range_meta = {
            key: float(value)
            for key, value in range_meta.get("x", {}).items()
            if isinstance(value, (int, float))
        }
        y_range_meta = {
            key: float(value)
            for key, value in range_meta.get("y", {}).items()
            if isinstance(value, (int, float))
        }

        return {
            "nodes": nodes,
            "edges": edges,
            "layout": layout,
            "x_range": x_range_meta,
            "y_range": y_range_meta,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_graph_payload(self, graph_data: Mapping[str, Any] | str) -> Mapping[str, Any]:
        if isinstance(graph_data, Mapping):
            return graph_data
        if isinstance(graph_data, str):
            if os.path.exists(graph_data):
                with open(graph_data, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            try:
                return json.loads(graph_data)
            except json.JSONDecodeError as exc:  # pragma: no cover
                raise ValueError("String graph data must be JSON serialisable") from exc
        if isinstance(graph_data, Iterable):
            return {"nodes": list(graph_data)}
        raise TypeError("graph_data must be a mapping, JSON string or path")

    def _build_graph(self, payload: Mapping[str, Any]) -> None:
        edges = self._extract_edges(payload)
        nodes = self._extract_nodes(payload, edges)

        for node_id, attributes in nodes.items():
            self._graph.add_node(node_id, **attributes)

        for edge in edges:
            source = str(edge["source"])
            target = str(edge["target"])

            strength = max(0.1, min(float(edge.get("strength", 1.0)), 1.0))
            edge_payload = dict(edge)
            edge_payload["strength"] = strength

            raw_weight = edge_payload.get("weight")
            weight_value: float
            try:
                weight_value = float(raw_weight) if raw_weight is not None else strength
            except (TypeError, ValueError):
                weight_value = strength

            # [FIX] honour backend-provided strengths directly when seeding
            # the layout so stronger ties pull nodes closer together while
            # weaker ties prefer longer springs (inverse weighting mirrors the
            # backend's force-directed assumptions).
            layout_weight = 1.0 / max(strength, 0.05)

            self._graph.add_edge(
                source,
                target,
                strength=strength,
                weight=weight_value,
                layout_weight=layout_weight,
                render_weight=weight_value,
                data=edge_payload,
            )

    def _extract_layout(self, payload: Mapping[str, Any]) -> Dict[str, Tuple[float, float]]:
        layout: Dict[str, Tuple[float, float]] = {}
        mapping = payload.get("layout")
        if isinstance(mapping, Mapping):
            for node_id, coords in mapping.items():
                if not isinstance(coords, Mapping):
                    continue
                x_val = coords.get("x")
                y_val = coords.get("y")
                try:
                    if x_val is None or y_val is None:
                        continue
                    layout[str(node_id)] = (float(x_val), float(y_val))
                except (TypeError, ValueError):
                    continue

        nodes = payload.get("nodes")
        if isinstance(nodes, Iterable):
            for node in nodes:
                if not isinstance(node, Mapping):
                    continue
                node_id = node.get("id") or node.get("name")
                if node_id is None:
                    continue
                x_val = node.get("x")
                y_val = node.get("y")
                try:
                    if x_val is None or y_val is None:
                        continue
                    layout[str(node_id)] = (float(x_val), float(y_val))
                except (TypeError, ValueError):
                    continue
        return layout

    def _select_layout(self, layout_payload: Mapping[str, Tuple[float, float]]) -> Dict[str, Tuple[float, float]]:
        node_ids = [str(node) for node in self._graph.nodes]
        if not node_ids:
            return {}

        provided: Dict[str, Tuple[float, float]] = {}
        for node_id in node_ids:
            coords = layout_payload.get(node_id)
            if coords is None:
                continue
            try:
                provided[node_id] = (float(coords[0]), float(coords[1]))
            except (TypeError, ValueError):
                logger.debug("Invalid coordinate payload for node %s; ignoring.", node_id)

        missing = [node_id for node_id in node_ids if node_id not in provided]
        if not missing and provided:
            return provided

        if missing:
            logger.warning(
                "Relationship payload missing coordinates for %d nodes; applying simple fallback placement.",
                len(missing),
            )

        layout: Dict[str, Tuple[float, float]] = dict(provided)
        if missing:
            layout.update(self._generate_fallback_layout(missing))

        if not layout:
            layout = self._generate_fallback_layout(node_ids)

        return layout

    def _generate_fallback_layout(self, node_ids: Sequence[str]) -> Dict[str, Tuple[float, float]]:
        """Generate a simple deterministic layout when no coordinates are supplied."""

        count = len(node_ids)
        if count == 0:
            return {}

        sorted_ids = [str(node) for node in node_ids]
        sorted_ids.sort()

        grid_size = max(1, int(math.ceil(math.sqrt(count))))
        spacing = 60.0
        half = (grid_size - 1) / 2.0
        rng = random.Random(42)

        fallback: Dict[str, Tuple[float, float]] = {}
        for index, node_id in enumerate(sorted_ids):
            row = index // grid_size
            col = index % grid_size
            x = (col - half) * spacing
            y = (row - half) * spacing
            jitter = spacing * 0.08
            x += rng.uniform(-jitter, jitter)
            y += rng.uniform(-jitter, jitter)
            fallback[node_id] = (float(x), float(y))

        return fallback

    def _extract_nodes(
        self,
        payload: Mapping[str, Any],
        edges: Sequence[MutableMapping[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        container = payload.get("nodes")
        if isinstance(container, Iterable):
            for raw in container:
                if not isinstance(raw, Mapping):
                    continue
                node_id = raw.get("id") or raw.get("name") or raw.get("agent")
                if node_id is None:
                    continue
                nodes[str(node_id)] = dict(raw)

        for edge in edges:
            for endpoint_key in ("source", "target"):
                endpoint = edge.get(endpoint_key)
                if endpoint is None:
                    continue
                node_id = str(endpoint)
                nodes.setdefault(node_id, {"id": node_id})
        return nodes

    def _extract_edges(self, payload: Mapping[str, Any]) -> List[MutableMapping[str, Any]]:
        for key in self._RELATIONSHIP_CONTAINER_KEYS:
            if key not in payload:
                continue
            container = self._maybe_parse_json(payload.get(key))
            edges = self._normalise_edge_container(container)
            if edges:
                return edges
        return self._extract_edges_from_nodes(payload)


    def _extract_or_compute_ranges(
        self,
        payload: Mapping[str, Any],
        layout: Mapping[str, Tuple[float, float]],
    ) -> Dict[str, Dict[str, float]]:
        """Use backend-provided range metadata when available."""

        def _normalise_range(mapping: Any) -> Optional[Dict[str, float]]:
            if not isinstance(mapping, Mapping):
                return None
            result: Dict[str, float] = {}
            for key in ("start", "end", "min", "max", "span"):
                value = mapping.get(key)
                if value is None:
                    continue
                try:
                    result[key] = float(value)
                except (TypeError, ValueError):
                    continue
            return result or None

        x_range = _normalise_range(payload.get("x_range"))
        y_range = _normalise_range(payload.get("y_range"))

        if x_range and y_range:
            return {"x": x_range, "y": y_range}

        return self._compute_range_metadata(layout)


    def _compute_force_directed_layout(
        self,
        *,
        initial_positions: Optional[Mapping[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        node_ids = [str(node) for node in self._graph.nodes]
        node_count = len(node_ids)  # [FIX] ensure node_count defined before use
        if node_count == 0:
            return {}

        rng = random.Random(42)

        initial_dict: Dict[str, Tuple[float, float]] = {}
        if initial_positions:
            for node, coords in initial_positions.items():
                try:
                    initial_dict[str(node)] = (float(coords[0]), float(coords[1]))
                except (TypeError, ValueError):
                    continue

        try:
            community_sets = list(
                nx_community.greedy_modularity_communities(
                    self._graph,
                    weight="strength",
                )
            )
        except Exception as exc:  # pragma: no cover - fallback safety
            logger.debug("Community detection failed; falling back to single cluster: %s", exc)
            community_sets = []

        if not community_sets:
            community_sets = [set(self._graph.nodes)]

        community_lookup: Dict[str, int] = {}
        for index, members in enumerate(community_sets):
            for node in members:
                community_lookup[str(node)] = index

        for u, v, data in self._graph.edges(data=True):
            strength = max(0.1, min(float(data.get("strength", 1.0)), 1.0))
            community_u = community_lookup.get(str(u), 0)
            community_v = community_lookup.get(str(v), 0)
            layout_weight = 1.0 / max(strength, 0.05)
            data["weight"] = layout_weight
            data["layout_weight"] = layout_weight
            data["inter_community"] = community_u != community_v
            data["layout_included"] = True

        global_initial: Dict[str, Tuple[float, float]] = {}
        try:
            raw_initial = nx.spring_layout(
                self._graph,
                weight="layout_weight",
                seed=42,
                k=max(4.0, 9.0 / math.sqrt(float(node_count))),
                iterations=max(2000, 8 * node_count),
                scale=max(20.0, math.sqrt(float(node_count)) * 6.0),
            )
            for node, coords in raw_initial.items():
                global_initial[str(node)] = (float(coords[0]), float(coords[1]))
        except Exception as exc:  # pragma: no cover - safeguard layout generation
            logger.warning(
                "Failed to compute initial spring layout for relationship graph: %s",
                exc,
            )

        for node_id, coords in global_initial.items():
            initial_dict.setdefault(node_id, coords)

        components = [sorted(component, key=str) for component in nx.connected_components(self._graph)]
        components.sort(key=lambda comp: (len(comp), [str(node) for node in comp]))

        forced_algorithm = self._layout_algorithm_choice
        component_infos: List[Dict[str, Any]] = []

        for component_nodes in components:
            comp_size = len(component_nodes)
            if comp_size == 0:
                continue

            subgraph = self._graph.subgraph(component_nodes).copy()
            component_initial = {
                node: coords
                for node, coords in initial_dict.items()
                if node in component_nodes
            }
            fixed_nodes = tuple(component_initial.keys()) if component_initial else None

            algorithm = forced_algorithm
            if algorithm is None:
                if comp_size <= 40:
                    algorithm = "kamada_kawai"
                elif comp_size <= 200:
                    algorithm = "fruchterman"
                else:
                    algorithm = "spring"

            layout_map: Dict[str, Tuple[float, float]] = {}

            if comp_size == 1:
                node_id = str(component_nodes[0])
                position = component_initial.get(node_id)
                if position is None:
                    angle = rng.random() * 2.0 * math.pi
                    radius = 2.5
                    position = (radius * math.cos(angle), radius * math.sin(angle))
                layout_map[node_id] = (float(position[0]), float(position[1]))
            else:
                if algorithm == "kamada_kawai":
                    scale = max(8.0, math.sqrt(float(comp_size)) * 3.0)
                    pos = nx.kamada_kawai_layout(
                        subgraph,
                        weight="weight",
                        pos=component_initial or None,
                        scale=scale,
                    )
                elif algorithm == "fruchterman":
                    k_value = max(5.0, 12.0 / math.sqrt(float(comp_size)))
                    iterations = max(5000, 10 * comp_size)
                    pos = nx.fruchterman_reingold_layout(
                        subgraph,
                        weight="weight",
                        pos=component_initial or None,
                        fixed=fixed_nodes,
                        k=k_value,
                        iterations=iterations,
                        seed=42,
                        scale=max(12.0, math.sqrt(float(comp_size)) * 4.5),
                    )
                else:
                    dynamic_k = max(6.0, 15.0 / math.sqrt(float(comp_size)))
                    iterations = max(6000, 10 * comp_size)
                    pos = nx.spring_layout(
                        subgraph,
                        weight="weight",
                        pos=component_initial or None,
                        fixed=fixed_nodes,
                        k=dynamic_k,
                        iterations=iterations,
                        seed=42,
                        scale=max(15.0, math.sqrt(float(comp_size)) * 5.0),
                    )

                for node in component_nodes:
                    key = node
                    coords = pos.get(key)
                    if coords is None:
                        coords = pos.get(str(key))
                    if coords is None and str(key).isdigit():
                        coords = pos.get(int(str(key)))
                    if coords is None:
                        continue
                    layout_map[str(node)] = (float(coords[0]), float(coords[1]))

                if len(layout_map) != comp_size:
                    missing_nodes = [str(node) for node in component_nodes if str(node) not in layout_map]
                    if missing_nodes:
                        logger.warning(
                            "Force-directed layout missing coordinates for nodes %s; applying circular fallback.",
                            sorted(missing_nodes),
                        )
                    radius = max(3.0, math.sqrt(float(comp_size)) * 1.8)
                    step = (2.0 * math.pi) / float(comp_size)
                    for index, node in enumerate(component_nodes):
                        angle = step * index
                        layout_map.setdefault(
                            str(node),
                            (
                                radius * math.cos(angle),
                                radius * math.sin(angle),
                            ),
                        )

            if not layout_map:
                continue

            xs = [coords[0] for coords in layout_map.values()]
            ys = [coords[1] for coords in layout_map.values()]
            centroid_x = sum(xs) / len(xs) if xs else 0.0
            centroid_y = sum(ys) / len(ys) if ys else 0.0

            centred_layout: Dict[str, Tuple[float, float]] = {}
            min_x = float("inf")
            max_x = float("-inf")
            min_y = float("inf")
            max_y = float("-inf")
            for node_id, (x_val, y_val) in layout_map.items():
                cx = float(x_val) - centroid_x
                cy = float(y_val) - centroid_y
                centred_layout[node_id] = (cx, cy)
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)

            span_x = max_x - min_x if math.isfinite(min_x) and math.isfinite(max_x) else 0.0
            span_y = max_y - min_y if math.isfinite(min_y) and math.isfinite(max_y) else 0.0
            diagonal = math.hypot(span_x, span_y)
            fallback_span = max(3.5, math.sqrt(float(comp_size)) * 2.2)
            if diagonal < 1e-6:
                diagonal = fallback_span
            margin = max(2.5, math.sqrt(float(comp_size)) * 0.9)
            radius = (diagonal * 0.5 * 1.5) + margin  # [FEATURE] component packing radius estimate

            is_isolated = subgraph.number_of_edges() == 0

            component_infos.append(
                {
                    "nodes": [str(node) for node in component_nodes],
                    "layout": centred_layout,
                    "size": comp_size,
                    "radius": max(radius, fallback_span * 0.5),
                    "span_x": span_x if span_x > 0 else fallback_span,
                    "span_y": span_y if span_y > 0 else fallback_span,
                    "isolated": is_isolated,
                    "initial_center": (
                        float(centroid_x),
                        float(centroid_y),
                    ),
                }
            )

        if not component_infos:
            return {}

        component_count = len(component_infos)
        centres: Dict[int, List[float]] = {}

        if component_count == 1:
            centres[0] = [0.0, 0.0]
        else:
            meta_graph = nx.Graph()
            for index, info in enumerate(component_infos):
                meta_graph.add_node(index, radius=info["radius"], size=info["size"])
            for i in range(component_count):
                for j in range(i + 1, component_count):
                    weight = 1.0 / (component_infos[i]["radius"] + component_infos[j]["radius"] + 1.0)
                    meta_graph.add_edge(i, j, weight=weight)

            initial_positions_meta: Dict[int, Tuple[float, float]] = {}
            max_radius = max(info["radius"] for info in component_infos)
            jitter_scale = max_radius * 0.15 + 20.0
            for index, info in enumerate(component_infos):
                base_cx, base_cy = info.get("initial_center", (0.0, 0.0))
                initial_positions_meta[index] = (
                    float(base_cx + rng.uniform(-jitter_scale, jitter_scale)),
                    float(base_cy + rng.uniform(-jitter_scale, jitter_scale)),
                )

            k_meta = max(10.0, max_radius * 2.2)
            pos_meta = nx.spring_layout(
                meta_graph,
                weight="weight",
                pos=initial_positions_meta,
                iterations=1200,
                seed=42,
                k=k_meta,
                scale=max_radius * max(component_count * 3.0, 8.0),
            )

            for index in range(component_count):
                coords = pos_meta.get(index, (0.0, 0.0))
                centres[index] = [float(coords[0]), float(coords[1])]

            # [FEATURE] iterative circle repulsion to avoid overlapping components
            for _ in range(800):
                adjusted = False
                for i in range(component_count):
                    centre_i = centres[i]
                    radius_i = component_infos[i]["radius"]
                    for j in range(i + 1, component_count):
                        centre_j = centres[j]
                        radius_j = component_infos[j]["radius"]
                        dx = centre_j[0] - centre_i[0]
                        dy = centre_j[1] - centre_i[1]
                        distance = math.hypot(dx, dy)
                        desired = max((radius_i + radius_j) * 3.5, 1.0)
                        if distance >= desired:
                            continue
                        if distance < 1e-6:
                            angle = rng.random() * 2.0 * math.pi
                            dx = math.cos(angle)
                            dy = math.sin(angle)
                            distance = 1.0
                        shift = desired - distance
                        ux = dx / distance
                        uy = dy / distance
                        centre_i[0] -= ux * shift
                        centre_i[1] -= uy * shift
                        centre_j[0] += ux * shift
                        centre_j[1] += uy * shift
                        adjusted = True
                if not adjusted:
                    break

        if component_count > 1:
            # [FEATURE] keep isolated nodes on an outer ring beyond the largest active component
            max_extent = 0.0
            for index, info in enumerate(component_infos):
                centre = centres.get(index, [0.0, 0.0])
                extent = math.hypot(centre[0], centre[1]) + info["radius"]
                if not info["isolated"]:
                    max_extent = max(max_extent, extent)

            base_ring = max(max_extent + 8.0, 10.0)
            for index, info in enumerate(component_infos):
                if not info["isolated"]:
                    continue
                centre = centres.setdefault(index, [0.0, 0.0])
                distance = math.hypot(centre[0], centre[1])
                desired = max(base_ring * 2.0, info["radius"] * 6.0)
                if distance < 1e-6:
                    angle = rng.random() * 2.0 * math.pi
                    centres[index] = [
                        desired * math.cos(angle),
                        desired * math.sin(angle),
                    ]
                elif distance < desired:
                    scale = desired / distance
                    centres[index][0] *= scale
                    centres[index][1] *= scale

        final_layout: Dict[str, Tuple[float, float]] = {}

        for index, info in enumerate(component_infos):
            centre = centres.get(index, [0.0, 0.0])
            offset_x, offset_y = centre
            for node_id, coords in info["layout"].items():
                final_layout[node_id] = (
                    float(coords[0] + offset_x),
                    float(coords[1] + offset_y),
                )

        if not final_layout:
            return {}

        xs = [coords[0] for coords in final_layout.values()]
        ys = [coords[1] for coords in final_layout.values()]
        span_x = max(xs) - min(xs) if xs else 0.0
        span_y = max(ys) - min(ys) if ys else 0.0
        global_span = max(span_x, span_y, 1.0)
        jitter_scale = max(global_span * 0.004, 0.05)

        buckets: Dict[Tuple[int, int], List[str]] = {}
        quant = max(global_span * 0.002, 0.02)
        for node_id, coords in final_layout.items():
            key = (int(math.floor(coords[0] / quant)), int(math.floor(coords[1] / quant)))
            buckets.setdefault(key, []).append(node_id)

        for nodes in buckets.values():
            if len(nodes) <= 1:
                continue
            for node_id in nodes:
                angle = rng.random() * 2.0 * math.pi
                radius = rng.random() * jitter_scale
                current = final_layout[node_id]
                final_layout[node_id] = (
                    float(current[0] + math.cos(angle) * radius),
                    float(current[1] + math.sin(angle) * radius),
                )

        return final_layout

    def _extract_edges_from_nodes(self, payload: Mapping[str, Any]) -> List[MutableMapping[str, Any]]:
        container = payload.get("nodes") or payload.get("agents")
        if not isinstance(container, Iterable):
            return []
        dedup: Dict[Tuple[str, str], MutableMapping[str, Any]] = {}
        for raw_node in container:
            if not isinstance(raw_node, Mapping):
                continue
            node_id = raw_node.get("id") or raw_node.get("name") or raw_node.get("agent")
            if node_id is None:
                continue
            node_id = str(node_id)
            for key, value in raw_node.items():
                key_lower = str(key).lower()
                if (
                    key_lower not in self._relationship_key_set
                    and not any(fragment in key_lower for fragment in self._RELATIONSHIP_KEY_SUBSTRINGS)
                ):
                    continue
                for entry in self._iter_connection_entries(value, default_source=node_id):
                    edge = self._normalise_edge(entry, default_source=node_id)
                    if edge is None:
                        continue
                    dedup_key = _EdgeKey(edge["source"], edge["target"]).as_tuple()
                    existing = dedup.get(dedup_key)
                    if existing is None or float(existing.get("strength", 0.0)) < float(edge.get("strength", 0.0)):
                        dedup[dedup_key] = edge
        return list(dedup.values())

    def _normalise_edge(
        self,
        raw: MutableMapping[str, Any],
        *,
        default_source: Optional[str] = None,
    ) -> Optional[MutableMapping[str, Any]]:
        if not isinstance(raw, MutableMapping):
            return None
        source = self._coerce_endpoint(raw, self._source_keys, default=default_source)
        target = self._coerce_endpoint(raw, self._target_keys)
        if source is None or target is None:
            return None
        normalised = dict(raw)
        normalised["source"] = source
        normalised["target"] = target
        normalised["strength"] = self._coerce_strength(normalised)
        return normalised

    def _normalise_edge_container(self, container: Any) -> List[MutableMapping[str, Any]]:
        results: List[MutableMapping[str, Any]] = []
        for entry in self._iter_connection_entries(container):
            edge = self._normalise_edge(entry)
            if edge is None:
                continue
            results.append(edge)
        return results

    def _iter_connection_entries(
        self,
        value: Any,
        *,
        default_source: Optional[str] = None,
    ) -> Iterator[MutableMapping[str, Any]]:
        value = self._maybe_parse_json(value)
        if isinstance(value, Mapping):
            if self._mapping_represents_edge(value) and self._coerce_endpoint(
                value, self._target_keys
            ):
                payload = dict(value)
                if default_source is not None and self._coerce_endpoint(payload, self._source_keys) is None:
                    payload["source"] = default_source
                yield payload
                return
            for key, nested in value.items():
                key_lower = str(key).lower()
                if key_lower in self._relationship_key_set or any(
                    fragment in key_lower for fragment in self._RELATIONSHIP_KEY_SUBSTRINGS
                ):
                    yield from self._iter_connection_entries(nested, default_source=default_source)
                elif isinstance(nested, Mapping) or (
                    isinstance(nested, Iterable) and not isinstance(nested, (str, bytes, bytearray))
                ):
                    yield from self._iter_connection_entries(nested, default_source=default_source)
            return

        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                yield from self._iter_connection_entries(item, default_source=default_source)
            return

        if value not in (None, "") and default_source is not None:
            yield {"source": default_source, "target": str(value)}

    def _mapping_represents_edge(self, mapping: Mapping[str, Any]) -> bool:
        keys = {str(key).lower() for key in mapping.keys()}
        has_target = any(alias in keys for alias in (key.lower() for key in self._target_keys))
        has_source = any(alias in keys for alias in (key.lower() for key in self._source_keys))
        return has_target or (has_source and "strength" in keys)

    @staticmethod
    def _maybe_parse_json(value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return value
            try:
                return json.loads(stripped)
            except ValueError:
                return value
        return value

    def _coerce_endpoint(
        self,
        payload: Mapping[str, Any],
        keys: Sequence[str],
        *,
        default: Optional[str] = None,
    ) -> Optional[str]:
        lowered: Optional[Dict[str, Any]] = None
        for key in keys:
            if key in payload:
                value = payload.get(key)
                if value is not None:
                    return str(value)
            lowered_key = key.lower()
            if lowered is None:
                lowered = {str(k).lower(): v for k, v in payload.items()}
            if lowered_key in lowered and lowered[lowered_key] is not None:
                return str(lowered[lowered_key])
        return default

    @staticmethod
    def _coerce_strength(payload: Mapping[str, Any]) -> float:
        for key in ("strength", "weight", "relationship_strength", "value", "score", "intimacy", "closeness"):
            if key not in payload:
                continue
            value = payload[key]
            try:
                return max(0.1, min(float(value), 1.0))
            except (TypeError, ValueError):
                continue
        return 1.0

    def _build_node_source(self, layout: Mapping[str, Tuple[float, float]]) -> ColumnDataSource:
        x_coords: List[float] = []
        y_coords: List[float] = []
        colors: List[str] = []
        alphas: List[float] = []
        sizes: List[float] = []
        labels: List[str] = []
        border_colors: List[str] = []
        node_ids: List[str] = []

        for node_id, attributes in self._graph.nodes(data=True):
            coords = layout.get(node_id, (0.0, 0.0))
            x_coords.append(float(coords[0]))
            y_coords.append(float(coords[1]))
            node_ids.append(str(node_id))
            colors.append(str(attributes.get("color") or attributes.get("fill_color") or "#2563EB"))
            border_colors.append(str(attributes.get("border_color") or attributes.get("line_color") or "#0F172A"))
            alphas.append(float(attributes.get("alpha", 1.0)))
            sizes.append(float(attributes.get("size", 22.0)))
            labels.append(str(attributes.get("label") or attributes.get("name") or node_id))

        return ColumnDataSource(
            data={
                "x": x_coords,
                "y": y_coords,
                "fill_color": colors,
                "line_color": border_colors,
                "alpha": alphas,
                "size": sizes,
                "label": labels,
                "id": node_ids,
            }
        )

    def _build_edge_source(self) -> Tuple[ColumnDataSource, Dict[str, List[float]]]:
        xs: List[List[float]] = []
        ys: List[List[float]] = []
        colors: List[str] = []
        alphas: List[float] = []
        widths: List[float] = []
        strengths: List[float] = []
        weights: List[float] = []
        sources: List[str] = []
        targets: List[str] = []
        backbone_flags: List[bool] = []

        self._edge_lookup.clear()

        global_span = max(
            float(self._range_metadata.get("x", {}).get("span", 0.0)),
            float(self._range_metadata.get("y", {}).get("span", 0.0)),
            1.0,
        )

        threshold = self._hide_weak_edges_threshold if self._hide_weak_edges_enabled else None

        for u, v, data in self._graph.edges(data=True):
            source_id = str(u)
            target_id = str(v)
            source_coords = self._layout.get(source_id)
            target_coords = self._layout.get(target_id)
            if source_coords is None or target_coords is None:
                logger.warning(
                    "Skipping relationship edge %s-%s due to missing coordinates.",
                    source_id,
                    target_id,
                )
                continue

            sx = float(source_coords[0])
            sy = float(source_coords[1])
            tx = float(target_coords[0])
            ty = float(target_coords[1])

            strength = max(0.1, min(float(data.get("strength", 1.0)), 1.0))

            if threshold is not None and strength < threshold:
                logger.debug(
                    "Skipping weak relationship edge %s-%s due to hide_weak_edges flag.",
                    source_id,
                    target_id,
                )
                continue

            edge_length = math.hypot(tx - sx, ty - sy)
            seed_bytes = f"{source_id}->{target_id}".encode("utf-8")
            seed = int.from_bytes(hashlib.sha256(seed_bytes).digest()[:8], "big", signed=False)
            edge_rng = random.Random(seed)

            raw = data.get("data") if isinstance(data, Mapping) else None
            is_backbone = False
            if isinstance(raw, Mapping):
                is_backbone = bool(raw.get("is_backbone"))

            if strength >= 0.75:
                xs.append([sx, tx])
                ys.append([sy, ty])
            elif strength >= 0.2:
                jitter_amount = max(edge_length * 0.02, global_span * 0.006)
                mid_x = (sx + tx) / 2.0
                mid_y = (sy + ty) / 2.0
                jitter_x = (edge_rng.random() * 2.0 - 1.0) * jitter_amount
                jitter_y = (edge_rng.random() * 2.0 - 1.0) * jitter_amount
                xs.append([sx, mid_x + jitter_x, tx])
                ys.append([sy, mid_y + jitter_y, ty])
            else:
                jitter_amount = max(edge_length * 0.035, global_span * 0.01)
                direction_x = tx - sx
                direction_y = ty - sy
                cp1_x = sx + direction_x / 3.0 + (edge_rng.random() * 2.0 - 1.0) * jitter_amount
                cp1_y = sy + direction_y / 3.0 + (edge_rng.random() * 2.0 - 1.0) * jitter_amount
                cp2_x = sx + (direction_x * 2.0 / 3.0) + (edge_rng.random() * 2.0 - 1.0) * jitter_amount
                cp2_y = sy + (direction_y * 2.0 / 3.0) + (edge_rng.random() * 2.0 - 1.0) * jitter_amount
                xs.append([sx, cp1_x, cp2_x, tx])
                ys.append([sy, cp1_y, cp2_y, ty])

            strengths.append(strength)
            raw_weight_value = None
            if isinstance(raw, Mapping):
                raw_weight_value = raw.get("weight")
            if raw_weight_value is None:
                raw_weight_value = data.get("render_weight", strength)
            try:
                weights.append(float(raw_weight_value))
            except (TypeError, ValueError):
                weights.append(float(strength))

            base_width = 1.0 + 2.5 * strength
            base_alpha = max(0.0, min(0.95, 0.3 + 0.6 * strength))
            if isinstance(raw, Mapping):
                try:
                    candidate_width = float(raw.get("line_width", base_width))
                    base_width = candidate_width
                except (TypeError, ValueError):
                    pass
                try:
                    candidate_alpha = float(raw.get("line_alpha", base_alpha))
                    base_alpha = candidate_alpha
                except (TypeError, ValueError):
                    pass

            if is_backbone:
                base_width = max(base_width, 2.5)
                base_alpha = min(0.95, max(base_alpha, 0.65))
            elif strength >= 0.75:
                base_width = max(base_width, 3.0)
                base_alpha = min(0.95, max(base_alpha, 0.7))
            elif strength >= 0.2:
                base_width = min(max(base_width, 1.2), 2.6)
                base_alpha = min(0.65, max(base_alpha, 0.35))
            else:
                base_width = min(0.8, max(base_width, 0.2))
                base_alpha = min(0.2, max(base_alpha, 0.05))

            widths.append(max(0.2, base_width))
            alphas.append(max(0.0, min(0.95, base_alpha)))
            colors.append("#64748B")
            sources.append(source_id)
            targets.append(target_id)
            backbone_flags.append(is_backbone)
            self._edge_lookup[_EdgeKey(u, v).as_tuple()] = len(xs) - 1

        source = ColumnDataSource(
            data={
                "xs": xs,
                "ys": ys,
                "line_color": colors,
                "line_alpha": alphas,
                "line_width": widths,
                "strength": strengths,
                "weight": weights,
                "source": sources,
                "target": targets,
                "is_backbone": backbone_flags,
            }
        )

        return source, {
            "line_color": list(colors),
            "line_alpha": list(alphas),
            "line_width": list(widths),
        }

    def _build_figure(
        self,
        width: int,
        height: int,
        x_range: Range1d,
        y_range: Range1d,
    ) -> Figure:
        fig = figure(
            width=width,
            height=height,
            x_range=x_range,
            y_range=y_range,
            x_axis_type="linear",
            y_axis_type="linear",
            tools="pan,wheel_zoom,reset,save",
            toolbar_location="right",
            background_fill_color=None,
            border_fill_color=None,
            sizing_mode="stretch_both",
        )
        fig.grid.visible = False
        fig.axis.visible = False
        fig.min_border = 0
        if "relationship-graph-figure" not in fig.css_classes:
            fig.css_classes.append("relationship-graph-figure")
        return fig

    def _attach_renderers(self) -> None:
        assert self.figure is not None
        edge_renderer = self.figure.multi_line(
            xs="xs",
            ys="ys",
            line_color="line_color",
            line_alpha="line_alpha",
            line_width="line_width",
            source=self.edge_source,
            line_join="round",
            line_cap="round",
        )
        edge_renderer.level = "underlay"

        node_renderer = self.figure.scatter(
            x="x",
            y="y",
            size="size",
            fill_color="fill_color",
            line_color="line_color",
            fill_alpha="alpha",
            line_alpha=1.0,
            line_width=1.5,
            source=self.node_source,
            hover_fill_color="fill_color",
            hover_line_color="#0F172A",
            hover_alpha=1.0,
        )

        edge_hover = HoverTool(
            renderers=[edge_renderer],
            tooltips=[("source", "@source"), ("target", "@target"), ("strength", "@strength{0.00}")],
        )
        node_hover = HoverTool(renderers=[node_renderer], tooltips=[("agent", "@label")])
        self.figure.add_tools(edge_hover, node_hover)

        tap_callback = CustomJS(
            args={"source": self.node_source},
            code="""
                if (!source || !source.selected) {
                    return;
                }
                const indices = source.selected.indices;
                if (!indices || indices.length === 0) {
                    return;
                }
                const index = indices[0];
                const data = source.data;
                const nodeId = data.id && data.id[index] !== undefined ? data.id[index] : null;
                const label = data.label && data.label[index] !== undefined ? data.label[index] : null;
                if (typeof window !== 'undefined') {
                    const detail = {};
                    if (nodeId !== null && nodeId !== undefined) {
                        detail.id = nodeId;
                    }
                    if (label !== null && label !== undefined) {
                        detail.label = label;
                    }
                    window.dispatchEvent(new CustomEvent('agentsociety:relationship-node', { detail }));
                }
            """,
        )
        tap_tool = TapTool(callback=tap_callback, renderers=[node_renderer])
        self.figure.add_tools(tap_tool)
        if self.figure.toolbar:
            self.figure.toolbar.active_tap = tap_tool

    def _make_flash_callback(
        self,
        index: int,
        key: Tuple[str, str],
        token: str,
        active: bool,
        *,
        final: bool = False,
    ):
        def _callback() -> None:
            if self._edge_tokens.get(key) != token:
                return
            base = self._base_edge_styles
            if active:
                width = base["line_width"][index] + self._highlight_width_delta
                alpha = min(0.95, base["line_alpha"][index] + 0.25)
                color = self._highlight_color
            else:
                width = base["line_width"][index]
                alpha = base["line_alpha"][index]
                color = base["line_color"][index]
            patch = {
                "line_color": [(index, color)],
                "line_alpha": [(index, alpha)],
                "line_width": [(index, width)],
            }
            self.edge_source.patch(patch)
            if final:
                self._edge_tokens.pop(key, None)

        return _callback

    def _compute_range_metadata(
        self, layout: Mapping[str, Tuple[float, float]]
    ) -> Dict[str, Dict[str, float]]:
        if not layout:
            min_x = -1.0
            max_x = 1.0
            min_y = -1.0
            max_y = 1.0
        else:
            xs = [coords[0] for coords in layout.values()]
            ys = [coords[1] for coords in layout.values()]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

        span_x = max(max_x - min_x, 0.0)
        span_y = max(max_y - min_y, 0.0)
        padding_x = span_x * 0.35 + 5.0
        padding_y = span_y * 0.35 + 5.0
        if span_x < 1e-6:
            padding_x = 0.5
        if span_y < 1e-6:
            padding_y = 0.5

        start_x = min_x - padding_x
        end_x = max_x + padding_x
        start_y = min_y - padding_y
        end_y = max_y + padding_y

        span_with_padding_x = max(end_x - start_x, 1e-6)
        span_with_padding_y = max(end_y - start_y, 1e-6)

        return {
            "x": {
                "min": float(min_x),
                "max": float(max_x),
                "start": float(start_x),
                "end": float(end_x),
                "span": float(span_with_padding_x),
            },
            "y": {
                "min": float(min_y),
                "max": float(max_y),
                "start": float(start_y),
                "end": float(end_y),
                "span": float(span_with_padding_y),
            },
        }

    @staticmethod
    def _build_plot_ranges(
        metadata: Mapping[str, Mapping[str, float]]
    ) -> Tuple[Range1d, Range1d]:
        x_meta = metadata.get("x", {})
        y_meta = metadata.get("y", {})
        x_start = float(x_meta.get("start", -3.0))
        x_end = float(x_meta.get("end", 3.0))
        y_start = float(y_meta.get("start", -3.0))
        y_end = float(y_meta.get("end", 3.0))
        return Range1d(x_start, x_end), Range1d(y_start, y_end)

    @staticmethod
    def _estimate_layout_scale(layout: Mapping[str, Tuple[float, float]]) -> float:
        if not layout:
            return 3.0
        xs = [coords[0] for coords in layout.values()]
        ys = [coords[1] for coords in layout.values()]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        return max(3.0, span)

    @staticmethod
    def _serialise_json(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        try:
            return json.loads(json.dumps(value))
        except (TypeError, ValueError):  # pragma: no cover
            return str(value)


__all__ = ["AgentRelationshipGraphRenderer"]
