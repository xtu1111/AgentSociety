"""Interactive agent relationship graph renderer.

This module exposes :class:`AgentRelationshipGraphRenderer`, a small helper that
builds a Bokeh force-directed style network visualization from flexible JSON
payloads.  It keeps two :class:`~bokeh.models.ColumnDataSource` instances for
nodes and edges respectively so that the figure can be updated incrementally
without being re-rendered from scratch.

The renderer already supported the following features before this change:

* Loading graph data from JSON dictionaries/strings/files with at least the
  ``source``/``target``/``strength`` edge keys.
* Building a `networkx.spring_layout` based layout for agents.
* Updating node colours externally (typically driven by agent emotion state).
* Falling back to a blank background when no map tile is supplied.

This file extends that behaviour with a flashing/highlighting animation for
edges, driven entirely from Python so that backend events (for example new
conversations) can trigger highlights without re-creating the figure.
"""

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
    Tuple,
)

import networkx as nx
from bokeh.document import Document
from bokeh.io import curdoc
from bokeh.models import ColumnDataSource, HoverTool, Range1d, TapTool, CustomJS
from bokeh.plotting import figure

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from bokeh.plotting import Figure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EdgeKey:
    """Immutable helper describing an undirected edge.

    Edges are treated as undirected for the highlight feature – the edge between
    Alice and Bob should be identical to the edge between Bob and Alice.
    """

    source: str
    target: str

    def __post_init__(self) -> None:  # pragma: no cover - dataclass hook
        # Normalize case and strip whitespace to make lookups forgiving.
        object.__setattr__(self, "source", str(self.source).strip())
        object.__setattr__(self, "target", str(self.target).strip())

    def as_tuple(self) -> Tuple[str, str]:
        s, t = sorted((self.source, self.target))
        return s, t


# ---------------------------------------------------------------------------
# Renderer implementation
# ---------------------------------------------------------------------------


class AgentRelationshipGraphRenderer:
    """Render and manage an interactive agent relationship graph.

    Parameters
    ----------
    graph_data:
        A JSON-compatible mapping, JSON string or path pointing to a JSON file
        describing the graph.  The structure is intentionally flexible – any
        object containing an iterable of edges with ``source``/``target`` and an
        optional ``strength`` field will work.  Nodes are inferred from edge
        endpoints when the input does not include them explicitly.
    doc:
        The Bokeh :class:`~bokeh.document.Document` that should own the figure.
        Defaults to :func:`~bokeh.io.curdoc` when omitted.
    width, height:
        Figure size in CSS pixels.
    highlight_color:
        Colour used while flashing an active edge.
    highlight_width_delta:
        Additional line width added to the base width when highlighting an edge.
    flash_count:
        Number of on/off flashes to perform for every activation.
    flash_interval:
        Interval between flashes in seconds.
    """

    def __init__(
        self,
        graph_data: Mapping[str, Any] | str,
        *,
        doc: Optional[Document] = None,
        width: int = 820,
        height: int = 620,
        highlight_color: str = "#ff7f0e",
        highlight_width_delta: float = 2.5,
        flash_count: int = 3,
        flash_interval: float = 0.25,
    ) -> None:
        self._document: Document = doc or curdoc()
        self._graph = nx.Graph()
        self._edge_lookup: Dict[Tuple[str, str], int] = {}
        self._edge_tokens: Dict[Tuple[str, str], str] = {}
        self._flash_count = max(1, flash_count)
        self._flash_interval = max(0.05, flash_interval)
        self._highlight_color = highlight_color
        self._highlight_width_delta = highlight_width_delta

        self._source_keys = (
            "source",
            "source_id",
            "from",
            "from_id",
            "agent",
            "agent_id",
            "id",
        )
        self._target_keys = (
            "target",
            "target_id",
            "to",
            "to_id",
            "friend",
            "friend_id",
            "agent",
            "agent_id",
        )

        parsed = self._load_graph_payload(graph_data)
        self._build_graph(parsed)

        raw_layout = nx.spring_layout(
            self._graph,
            seed=42,
            weight="weight",
        )
        layout = {
            node: (float(position[0]), float(position[1]))
            for node, position in raw_layout.items()
        }
        self._layout = layout
        node_source = self._build_node_source(layout)
        edge_source, base_styles = self._build_edge_source(layout)
        x_range, y_range = self._compute_plot_ranges(layout)

        self.node_source = node_source
        self.edge_source = edge_source
        self._base_edge_styles = base_styles

        self.figure = self._build_figure(width, height, x_range, y_range)
        self._attach_renderers()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def activate_edge(self, source: str, target: str) -> bool:
        """Flash the edge connecting ``source`` and ``target``.

        Returns ``True`` when an edge exists and the highlight animation was
        scheduled, ``False`` otherwise.  When multiple events hit the same edge
        the newest activation supersedes any pending animation to avoid
        conflicting patches.
        """

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

        # Ensure the last callback always restores the base style exactly.
        reset_timeout = int(total_steps * self._flash_interval * 1000)
        self._document.add_timeout_callback(
            self._make_flash_callback(index, key, token, False, final=True),
            reset_timeout,
        )
        return True

    def export_graph(self) -> Dict[str, Any]:
        """Serialise the graph nodes, edges and spring-layout coordinates."""

        node_data = self.node_source.data
        nodes: List[Dict[str, Any]] = []
        layout: Dict[str, Dict[str, float]] = {}

        for index, node_id in enumerate(node_data.get("id", [])):
            x_coord = float(node_data["x"][index])
            y_coord = float(node_data["y"][index])

            payload: Dict[str, Any] = {
                "id": node_id,
                "x": x_coord,
                "y": y_coord,
                "color": node_data["fill_color"][index],
                "border_color": node_data["line_color"][index],
                "alpha": float(node_data["alpha"][index]),
                "size": float(node_data["size"][index]),
            }

            label_column = node_data.get("label")
            if label_column is not None and index < len(label_column):
                payload["label"] = label_column[index]

            graph_attributes = self._graph.nodes.get(node_id, {})
            for key, value in graph_attributes.items():
                if key in payload:
                    continue
                payload[key] = self._serialise_json(value)

            nodes.append(payload)
            layout[node_id] = {"x": x_coord, "y": y_coord}

        edge_data = self.edge_source.data
        edges: List[Dict[str, Any]] = []
        xs_column = edge_data.get("xs", [])
        ys_column = edge_data.get("ys", [])

        for index, source in enumerate(edge_data.get("source", [])):
            target = edge_data["target"][index]
            payload: Dict[str, Any] = {
                "source": source,
                "target": target,
                "strength": float(edge_data["strength"][index]),
            }

            if index < len(xs_column) and index < len(ys_column):
                try:
                    path_xs = [float(coord) for coord in xs_column[index]]
                    path_ys = [float(coord) for coord in ys_column[index]]
                except (TypeError, ValueError):
                    path_xs, path_ys = [], []
                payload["xs"] = path_xs
                payload["ys"] = path_ys
                if len(path_xs) >= 2 and len(path_ys) >= 2:
                    payload["x0"], payload["x1"] = path_xs[0], path_xs[-1]
                    payload["y0"], payload["y1"] = path_ys[0], path_ys[-1]

            weight_value: Optional[float] = None
            weights_column = edge_data.get("weight")
            if weights_column is not None:
                try:
                    weight_value = float(weights_column[index])
                except (TypeError, ValueError, KeyError, IndexError):
                    weight_value = None

            attributes = self._graph.get_edge_data(source, target, {})
            if weight_value is None:
                raw_weight = attributes.get("weight")
                if raw_weight is not None:
                    try:
                        weight_value = float(raw_weight)
                    except (TypeError, ValueError):
                        weight_value = None

            if weight_value is None:
                weight_value = payload["strength"]

            payload["weight"] = weight_value
            for key, value in attributes.items():
                if key == "data":
                    payload[key] = self._serialise_json(value)
                elif key not in {"strength", "weight"}:
                    payload.setdefault(key, self._serialise_json(value))

            edges.append(payload)

        return {"nodes": nodes, "edges": edges, "layout": layout}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_flash_callback(
        self,
        index: int,
        key: Tuple[str, str],
        token: str,
        active: bool,
        final: bool = False,
    ):
        def _callback() -> None:
            if self._edge_tokens.get(key) != token:
                return
            if final:
                self._edge_tokens.pop(key, None)
            self._set_edge_state(index, active)

        return _callback

    def _set_edge_state(self, index: int, active: bool) -> None:
        base = self._base_edge_styles
        color = (
            self._highlight_color if active else base["line_color"][index]
        )
        alpha = 0.9 if active else base["line_alpha"][index]
        width = (
            base["line_width"][index] + self._highlight_width_delta
            if active
            else base["line_width"][index]
        )

        patch = {
            "line_color": [(index, color)],
            "line_alpha": [(index, alpha)],
            "line_width": [(index, width)],
        }
        self._document.add_next_tick_callback(
            lambda patch=patch: self.edge_source.patch(patch)
        )

    def _load_graph_payload(
        self, graph_data: Mapping[str, Any] | str
    ) -> Mapping[str, Any]:
        if isinstance(graph_data, Mapping):
            return graph_data
        if isinstance(graph_data, str):
            if os.path.exists(graph_data):
                with open(graph_data, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            try:
                return json.loads(graph_data)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ValueError("String graph data must be JSON serialisable") from exc
        if isinstance(graph_data, Iterable):
            # Agent profile uploads are often stored as a bare list of agents.
            # Treat those as a node container and let the edge extractor infer
            # the relationship list from embedded "connections" fields.
            return {"nodes": list(graph_data)}
        raise TypeError("graph_data must be a mapping, JSON string or path")

    def _build_graph(self, payload: Mapping[str, Any]) -> None:
        edges = self._extract_edges(payload)
        nodes = self._extract_nodes(payload, edges)

        for node_id, node_payload in nodes.items():
            self._graph.add_node(node_id, **node_payload)

        for edge in edges:
            source = str(edge["source"])
            target = str(edge["target"])
            strength = float(edge.get("strength", 1.0))
            # Clamp strength to the expected [0.1, 1] range so that the spring
            # layout weighting remains well-behaved even when data sources send
            # slightly out-of-band values.
            strength = max(0.1, min(strength, 1.0))
            self._graph.add_edge(
                source,
                target,
                strength=strength,
                weight=strength,
                data=edge,
            )

    def _extract_edges(
        self, payload: Mapping[str, Any]
    ) -> List[MutableMapping[str, Any]]:
        for key in (
            "edges",
            "relationships",
            "connections",
            "links",
            "social_network",
        ):
            if key in payload and isinstance(payload[key], Iterable):
                raw_edges = list(self._iter_connection_entries(payload[key]))
                edges = [
                    edge
                    for raw in raw_edges
                    if isinstance(raw, MutableMapping)
                    for edge in [self._normalise_edge(raw)]
                    if edge is not None
                ]
                if edges:
                    break
        else:
            edges = []

        if not edges:
            # Fallback for agent profile schemas where connections live inside
            # each node entry (``agent["connections"]`` or similar).
            edges = self._extract_edges_from_nodes(payload)

        return edges

    def _extract_edges_from_nodes(
        self, payload: Mapping[str, Any]
    ) -> List[MutableMapping[str, Any]]:
        node_container = None
        for key in ("nodes", "agents", "people"):
            value = payload.get(key)
            if isinstance(value, Iterable):
                node_container = value
                break

        if node_container is None:
            return []

        dedup: Dict[Tuple[str, str], MutableMapping[str, Any]] = {}
        for node in node_container:
            if not isinstance(node, Mapping):
                continue
            node_id = (
                node.get("id")
                or node.get("name")
                or node.get("agent")
                or node.get("agent_id")
            )
            if node_id is None:
                continue
            node_id = str(node_id)

            for rel_key in (
                "connections",
                "relationships",
                "links",
                "edges",
                "social_network",
            ):
                maybe_connections = node.get(rel_key)
                if not isinstance(maybe_connections, Iterable):
                    continue
                for rel in self._iter_connection_entries(
                    maybe_connections, default_source=node_id
                ):
                    if not isinstance(rel, MutableMapping):
                        continue

                    merged = self._normalise_edge(rel, default_source=node_id)
                    if not merged:
                        continue

                    key = _EdgeKey(merged["source"], merged["target"]).as_tuple()
                    # Prefer the strongest relationship if duplicates exist.
                    existing = dedup.get(key)
                    if existing is None or float(existing.get("strength", 0.0)) < float(
                        merged.get("strength", 0.0)
                    ):
                        dedup[key] = merged

        return list(dedup.values())

    def _normalise_edge(
        self,
        raw: MutableMapping[str, Any],
        *,
        default_source: Optional[str] = None,
    ) -> Optional[MutableMapping[str, Any]]:
        """Convert arbitrary edge payloads into ``source``/``target`` pairs."""

        source_keys = ["source", "source_id", "from", "from_id"]
        if default_source is not None:
            source_keys.extend(["agent", "agent_id", "id"])
        else:
            source_keys.extend(["agent", "agent_id"])
        target_keys = ["target", "target_id", "to", "to_id", "friend", "friend_id", "agent"]

        source = self._coerce_endpoint(raw, source_keys, default=default_source)
        target = self._coerce_endpoint(raw, target_keys)

        if source is None or target is None:
            return None

        normalised: MutableMapping[str, Any] = dict(raw)
        normalised["source"] = source
        normalised["target"] = target
        normalised["strength"] = self._coerce_strength(normalised)
        return normalised

    @staticmethod
    def _coerce_endpoint(
        raw: Mapping[str, Any], keys: Iterable[str], *, default: Optional[str] = None
    ) -> Optional[str]:
        for key in keys:
            if key in raw and raw[key] not in (None, ""):
                return str(raw[key])
        return str(default) if default is not None else None

    @staticmethod
    def _coerce_strength(raw: Mapping[str, Any]) -> float:
        for key in (
            "strength",
            "weight",
            "value",
            "score",
            "relationship_strength",
            "intimacy",
            "closeness",
        ):
            if key in raw and raw[key] not in (None, ""):
                try:
                    return float(raw[key])
                except (TypeError, ValueError):
                    continue
        return 1.0

    def _iter_connection_entries(
        self, value: Any, *, default_source: Optional[str] = None
    ) -> Iterator[MutableMapping[str, Any]]:
        """Yield mutable mappings describing edges from flexible containers."""

        if isinstance(value, Mapping):
            if self._mapping_represents_edge(value, default_source):
                payload = dict(value)
                if default_source is not None:
                    payload.setdefault("source", default_source)
                yield payload
                return

            for key, nested in value.items():
                key_str = str(key)
                nested_source = default_source or key_str

                if isinstance(nested, Mapping):
                    if self._mapping_represents_edge(nested, nested_source):
                        payload = dict(nested)
                        payload.setdefault("target", key_str)
                        if default_source is not None:
                            payload.setdefault("source", default_source)
                        else:
                            payload.setdefault("source", nested_source)
                        yield payload
                        continue

                if isinstance(nested, Mapping) or (
                    isinstance(nested, Iterable)
                    and not isinstance(nested, (str, bytes, bytearray))
                ):
                    for candidate in self._iter_connection_entries(
                        nested, default_source=nested_source
                    ):
                        if "target" not in candidate:
                            candidate["target"] = key_str
                        if default_source is not None and "source" not in candidate:
                            candidate["source"] = default_source
                        elif default_source is None and "source" not in candidate:
                            candidate["source"] = nested_source
                        yield candidate
                    continue

                if nested in (None, ""):
                    continue

                payload: MutableMapping[str, Any] = {"target": key_str}
                try:
                    payload["strength"] = float(nested)
                except (TypeError, ValueError):
                    payload["strength"] = nested
                if default_source is not None:
                    payload["source"] = default_source
                else:
                    payload["source"] = nested_source
                yield payload
            return

        if isinstance(value, Iterable) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                if isinstance(item, Mapping):
                    payload = dict(item)
                    if default_source is not None:
                        payload.setdefault("source", default_source)
                    yield payload
                elif isinstance(item, Iterable) and not isinstance(
                    item, (str, bytes, bytearray)
                ):
                    sequence = list(item)
                    if not sequence:
                        continue
                    payload: MutableMapping[str, Any] = {
                        "target": str(sequence[0])
                    }
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
            payload = {"source": default_source, "target": str(value)}
            yield payload

    def _mapping_represents_edge(
        self, mapping: Mapping[str, Any], default_source: Optional[str]
    ) -> bool:
        keys = {str(key).lower() for key in mapping.keys()}
        has_target = any(candidate in keys for candidate in self._target_keys)
        if not has_target and "agent" in keys:
            has_target = True
        has_source = any(candidate in keys for candidate in self._source_keys)
        if not has_source and default_source is not None:
            has_source = True
        return has_source and has_target

    @staticmethod
    def _serialise_json(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, Mapping):
            return {
                str(key): AgentRelationshipGraphRenderer._serialise_json(val)
                for key, val in value.items()
            }
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            return [AgentRelationshipGraphRenderer._serialise_json(item) for item in value]
        return str(value)

    def _extract_nodes(
        self,
        payload: Mapping[str, Any],
        edges: Iterable[MutableMapping[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        node_container = None
        for key in ("nodes", "agents", "people"):
            value = payload.get(key)
            if isinstance(value, Iterable):
                node_container = value
                break

        nodes: Dict[str, Dict[str, Any]] = {}
        if node_container is not None:
            for raw in node_container:
                if not isinstance(raw, Mapping):
                    continue
                node_id = raw.get("id") or raw.get("name") or raw.get("agent")
                if node_id is None:
                    continue
                nodes[str(node_id)] = dict(raw)

        for edge in edges:
            for endpoint_key in ("source", "target"):
                endpoint = str(edge[endpoint_key])
                nodes.setdefault(endpoint, {"id": endpoint})
        return nodes

    @staticmethod
    def _resolve_sentiment(payload: Any) -> Optional[float]:
        if payload is None:
            return None
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            return float(payload)
        if isinstance(payload, str):
            value = payload.strip()
            if not value:
                return None
            try:
                return float(value)
            except ValueError:
                try:
                    parsed = json.loads(value)
                except Exception:  # pragma: no cover - best effort decoding
                    return None
                return AgentRelationshipGraphRenderer._resolve_sentiment(parsed)
        if isinstance(payload, Mapping):
            for key in ("sentiment", "status", "value", "score"):
                if key in payload:
                    nested = AgentRelationshipGraphRenderer._resolve_sentiment(payload[key])
                    if nested is not None:
                        return nested
            return None
        if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes, bytearray)):
            for item in payload:
                nested = AgentRelationshipGraphRenderer._resolve_sentiment(item)
                if nested is not None:
                    return nested
        return None

    @staticmethod
    def _sentiment_to_colour(sentiment: Optional[float]) -> str:
        if sentiment is None:
            return "#00FF00"
        if sentiment >= 0.2:
            return "#0000FF"
        if sentiment <= -0.2:
            return "#FF0000"
        return "#00FF00"

    def _build_node_source(self, layout: Mapping[str, Tuple[float, float]]):
        ids: List[str] = []
        xs: List[float] = []
        ys: List[float] = []
        colors: List[str] = []
        border_colors: List[str] = []
        alphas: List[float] = []
        sizes: List[float] = []

        labels: List[str] = []
        for node_id, attributes in self._graph.nodes(data=True):
            ids.append(node_id)
            coords = layout.get(node_id)
            if coords is None:
                coords = (0.0, 0.0)
            x_coord = float(coords[0])
            y_coord = float(coords[1])
            xs.append(x_coord)
            ys.append(y_coord)
            sentiment = None
            if "status" in attributes:
                sentiment = self._resolve_sentiment(attributes.get("status"))
            if sentiment is None and "sentiment" in attributes:
                sentiment = self._resolve_sentiment(attributes.get("sentiment"))
            color_value = attributes.get("color")
            if color_value in (None, ""):
                color_value = self._sentiment_to_colour(sentiment)
            colors.append(str(color_value))
            border_value = attributes.get("border_color")
            if border_value in (None, ""):
                border_value = color_value
            border_colors.append(str(border_value))
            alpha_value = attributes.get("alpha", 0.95)
            try:
                alpha_numeric = float(alpha_value)
            except (TypeError, ValueError):
                alpha_numeric = 0.95
            alphas.append(alpha_numeric)
            base_size = attributes.get("size", 26)
            try:
                base_size = float(base_size)
            except (TypeError, ValueError):
                base_size = 26.0
            base_size = max(18.0, base_size)
            sizes.append(base_size)
            label_value = attributes.get("label") or attributes.get("name") or node_id
            labels.append(str(label_value))

        source = ColumnDataSource(
            data={
                "id": ids,
                "x": xs,
                "y": ys,
                "fill_color": colors,
                "line_color": border_colors,
                "alpha": alphas,
                "size": sizes,
                "label": labels,
            }
        )
        return source

    def _build_edge_source(
        self, layout: Mapping[str, Tuple[float, float]]
    ) -> Tuple[ColumnDataSource, Dict[str, List[float]]]:
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
            start = layout.get(u)
            end = layout.get(v)
            if start is None or end is None:
                continue
            x0 = float(start[0])
            y0 = float(start[1])
            x1 = float(end[0])
            y1 = float(end[1])
            xs.append([x0, x1])
            ys.append([y0, y1])
            strength = float(data.get("strength", 1.0))
            strengths.append(strength)
            weights.append(float(data.get("weight", strength)))
            base_width = 1.75 + strength * 2.75
            widths.append(base_width)
            alpha = min(0.95, 0.5 + strength * 0.35)
            alphas.append(alpha)
            colors.append("#64748B")
            sources.append(str(u))
            targets.append(str(v))

            list_index = len(xs) - 1
            self._edge_lookup[_EdgeKey(u, v).as_tuple()] = list_index

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

        base_styles = {
            "line_color": list(colors),
            "line_alpha": list(alphas),
            "line_width": list(widths),
        }
        return source, base_styles

    def _build_figure(
        self,
        width: int,
        height: int,
        x_range: Range1d,
        y_range: Range1d,
    ) -> "Figure":
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
            tooltips=[
                ("source", "@source"),
                ("target", "@target"),
                ("strength", "@strength{0.00}")
            ],
        )
        node_hover = HoverTool(
            renderers=[node_renderer],
            tooltips=[("agent", "@label")],
        )
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

    def _compute_plot_ranges(
        self, layout: Mapping[str, Tuple[float, float]]
    ) -> Tuple[Range1d, Range1d]:
        if not layout:
            return Range1d(-1.25, 1.25), Range1d(-1.25, 1.25)

        xs = [float(coords[0]) for coords in layout.values()]
        ys = [float(coords[1]) for coords in layout.values()]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        span_x = max_x - min_x
        span_y = max_y - min_y

        pad_x = max(span_x * 0.15, 0.1)
        pad_y = max(span_y * 0.15, 0.1)

        if span_x == 0:
            min_x -= 0.5
            max_x += 0.5
        else:
            min_x -= pad_x
            max_x += pad_x

        if span_y == 0:
            min_y -= 0.5
            max_y += 0.5
        else:
            min_y -= pad_y
            max_y += pad_y

        return Range1d(min_x, max_x), Range1d(min_y, max_y)

__all__ = ["AgentRelationshipGraphRenderer"]