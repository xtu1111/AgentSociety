"""Interactive agent relationship graph renderer."""

from __future__ import annotations

import json
import logging
import os
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

        layout = self._extract_layout(payload)
        node_layout: Dict[str, Tuple[float, float]] = {}
        missing_nodes: List[str] = []
        for node in self._graph.nodes:
            coords = layout.get(node)
            if coords is None:
                missing_nodes.append(node)
                coords = (0.0, 0.0)
            node_layout[node] = coords

        if missing_nodes:
            logger.warning(
                "Relationship payload missing coordinates for %d nodes; defaulting to origin.",
                len(missing_nodes),
            )

        self._layout = node_layout
        self._layout_scale = self._estimate_layout_scale(node_layout)

        self.node_source = self._build_node_source(node_layout)
        self.edge_source, self._base_edge_styles = self._build_edge_source()
        self.figure: Optional[Figure] = None

        if self._enable_rendering:
            x_range, y_range = self._compute_plot_ranges(node_layout)
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
            payload = {
                "source": source,
                "target": target,
                "strength": float(edge_data["strength"][idx]),
                "line_width": float(edge_data["line_width"][idx]),
                "line_alpha": float(edge_data["line_alpha"][idx]),
            }
            if idx < len(xs_column) and idx < len(ys_column):
                payload["xs"] = [float(v) for v in xs_column[idx]]
                payload["ys"] = [float(v) for v in ys_column[idx]]
            edges.append(payload)

        return {"nodes": nodes, "edges": edges, "layout": layout}

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
            self._graph.add_edge(
                source,
                target,
                strength=strength,
                weight=strength,
                data=dict(edge),
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

        for u, v, data in self._graph.edges(data=True):
            raw = data.get("data") if isinstance(data, Mapping) else None
            xs_payload: List[float] = []
            ys_payload: List[float] = []
            if isinstance(raw, Mapping):
                xs_payload = self._coerce_path(raw.get("xs"))
                ys_payload = self._coerce_path(raw.get("ys"))
            if not xs_payload or not ys_payload:
                logger.warning(
                    "Relationship payload missing geometry for edge %s-%s; rendering disabled.",
                    u,
                    v,
                )
                xs.append([])
                ys.append([])
            else:
                xs.append([xs_payload[0], xs_payload[-1]])
                ys.append([ys_payload[0], ys_payload[-1]])

            strength = float(data.get("strength", 1.0))
            strengths.append(strength)
            weights.append(float(data.get("weight", strength)))
            base_width = 1.0 + 2.5 * strength
            base_alpha = max(0.0, min(0.9, 0.3 + 0.6 * strength))
            if isinstance(raw, Mapping):
                try:
                    base_width = float(raw.get("line_width", base_width))
                except (TypeError, ValueError):
                    base_width = 1.0 + 2.5 * strength
                try:
                    base_alpha = float(raw.get("line_alpha", base_alpha))
                except (TypeError, ValueError):
                    base_alpha = max(0.0, min(0.9, 0.3 + 0.6 * strength))
            widths.append(max(0.2, base_width))
            alphas.append(max(0.0, min(0.95, base_alpha)))
            colors.append("#64748B")
            sources.append(str(u))
            targets.append(str(v))
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
            }
        )

        return source, {
            "line_color": list(colors),
            "line_alpha": list(alphas),
            "line_width": list(widths),
        }

    @staticmethod
    def _coerce_path(values: Any) -> List[float]:
        if not isinstance(values, Iterable) or isinstance(values, (str, bytes, bytearray)):
            return []
        result: List[float] = []
        for entry in values:
            try:
                result.append(float(entry))
            except (TypeError, ValueError):
                continue
        return result

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

    def _compute_plot_ranges(
        self, layout: Mapping[str, Tuple[float, float]]
    ) -> Tuple[Range1d, Range1d]:
        if not layout:
            return Range1d(-3.0, 3.0), Range1d(-3.0, 3.0)
        xs = [coords[0] for coords in layout.values()]
        ys = [coords[1] for coords in layout.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        half_width = max(span_x * 0.6, 1.2)
        half_height = max(span_y * 0.6, 1.2)
        return Range1d(center_x - half_width, center_x + half_width), Range1d(
            center_y - half_height,
            center_y + half_height,
        )

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
