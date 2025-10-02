import React, {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { fetchCustom } from '../../components/fetch';
import type { Agent } from './components/type';

interface NodeSelectDetail {
    id?: string;
    label?: string;
}

interface RelationshipGraphPanelProps {
    experimentId?: string;
    visible: boolean;
    agents: Agent[];
    onNodeSelect?: (detail: NodeSelectDetail) => void;
}

interface RawRelationshipNode {
    id?: unknown;
    name?: unknown;
    label?: unknown;
    color?: unknown;
    size?: unknown;
    x?: unknown;
    y?: unknown;
}

interface RawRelationshipEdge {
    source?: unknown;
    target?: unknown;
    strength?: unknown;
    xs?: unknown;
    ys?: unknown;
}

interface RelationshipEdgesResponse {
    nodes?: RawRelationshipNode[];
    edges?: RawRelationshipEdge[];
    layout?: Record<string, { x?: unknown; y?: unknown }>;
}

interface GraphNodeInput {
    id: string;
    label: string;
    agentId?: number;
    sentiment?: number;
    color: string;
    size: number;
    layoutX?: number;
    layoutY?: number;
}

interface GraphEdgeInput {
    source: string;
    target: string;
    strength: number;
    xs?: number[];
    ys?: number[];
}

interface LayoutNode extends GraphNodeInput {
    x: number;
    y: number;
}

interface LayoutEdge {
    key: string;
    source: string;
    target: string;
    strength: number;
    points: { x: number; y: number }[];
    baseWidth: number;
    baseOpacity: number;
}

interface HighlightEventDetail {
    source?: string | number;
    target?: string | number;
}

const DEFAULT_STRENGTH = 0.4;
const DEFAULT_NODE_SIZE = 24;
const HIGHLIGHT_DURATION_MS = 600;
const DOM_DELTA_LINE = 1;
const DOM_DELTA_PAGE = 2;

const normaliseToken = (value: unknown): string | undefined => {
    if (value === null || value === undefined) {
        return undefined;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed === '' ? undefined : trimmed;
    }
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) {
            return undefined;
        }
        return String(value);
    }
    return String(value);
};

const parseNumeric = (value: unknown): number | undefined => {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string') {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : undefined;
    }
    return undefined;
};

const parseCoordinateArray = (value: unknown): number[] | undefined => {
    if (!Array.isArray(value)) {
        return undefined;
    }
    const coords = value
        .map((entry) => parseNumeric(entry))
        .filter((entry): entry is number => entry !== undefined);
    return coords.length >= 2 ? coords : undefined;
};

const clampStrength = (value: number | undefined): number => {
    if (typeof value !== 'number' || Number.isNaN(value)) {
        return DEFAULT_STRENGTH;
    }
    if (value < 0.1) {
        return 0.1;
    }
    if (value > 1) {
        return 1;
    }
    return value;
};

const normaliseStrength = (value: unknown): number | undefined => {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string') {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
};

const makeEdgeKey = (a: string, b: string): string => [a, b].sort().join('::');

const resolveSentiment = (status: unknown): number | undefined => {
    if (status === null || status === undefined) {
        return undefined;
    }
    if (typeof status === 'number') {
        return status;
    }
    if (typeof status === 'string') {
        const numeric = Number(status);
        if (!Number.isNaN(numeric)) {
            return numeric;
        }
        try {
            const parsed = JSON.parse(status);
            return resolveSentiment(parsed);
        } catch (err) {
            console.error('failed to parse sentiment string', err);
            return undefined;
        }
    }
    if (typeof status === 'object') {
        if (status && 'sentiment' in status) {
            return resolveSentiment((status as Record<string, unknown>).sentiment);
        }
        if (status && 'status' in status) {
            return resolveSentiment((status as Record<string, unknown>).status);
        }
    }
    return undefined;
};

const sentimentToColour = (sentiment: number | undefined): string => {
    if (typeof sentiment !== 'number' || Number.isNaN(sentiment)) {
        return '#00FF00';
    }
    if (sentiment >= 0.2) {
        return '#0000FF';
    }
    if (sentiment <= -0.2) {
        return '#FF0000';
    }
    return '#00FF00';
};

interface SimulationNode extends GraphNodeInput {
    x: number;
    y: number;
    vx: number;
    vy: number;
}

const runForceLayout = (
    nodes: GraphNodeInput[],
    edges: GraphEdgeInput[],
    width: number,
    height: number,
    previousPositions: Map<string, { x: number; y: number }>,
): LayoutNode[] => {
    if (nodes.length === 0 || width <= 0 || height <= 0) {
        return [];
    }

    const simulationNodes: SimulationNode[] = nodes.map((node) => {
        const previous = previousPositions.get(node.id);
        const startX = previous?.x ?? (width / 2 + (Math.random() - 0.5) * width * 0.25);
        const startY = previous?.y ?? (height / 2 + (Math.random() - 0.5) * height * 0.25);
        return {
            ...node,
            x: startX,
            y: startY,
            vx: 0,
            vy: 0,
        };
    });

    const lookup = new Map<string, SimulationNode>();
    simulationNodes.forEach((node) => {
        lookup.set(node.id, node);
    });

    const area = Math.max(width * height, 1);
    const baseLength = Math.sqrt(area / simulationNodes.length);
    let temperature = Math.min(width, height) / 6;
    const iterations = Math.min(300, Math.max(80, edges.length * 12));

    for (let iter = 0; iter < iterations; iter += 1) {
        simulationNodes.forEach((node) => {
            node.vx = 0;
            node.vy = 0;
        });

        for (let i = 0; i < simulationNodes.length; i += 1) {
            for (let j = i + 1; j < simulationNodes.length; j += 1) {
                const nodeA = simulationNodes[i];
                const nodeB = simulationNodes[j];
                let dx = nodeA.x - nodeB.x;
                let dy = nodeA.y - nodeB.y;
                let distance = Math.sqrt(dx * dx + dy * dy);
                if (distance < 1e-6) {
                    dx = (Math.random() - 0.5) * 0.1;
                    dy = (Math.random() - 0.5) * 0.1;
                    distance = Math.sqrt(dx * dx + dy * dy) || 0.01;
                }
                const force = (baseLength * baseLength) / distance;
                const fx = (dx / distance) * force;
                const fy = (dy / distance) * force;
                nodeA.vx += fx;
                nodeA.vy += fy;
                nodeB.vx -= fx;
                nodeB.vy -= fy;
            }
        }

        for (const edge of edges) {
            const source = lookup.get(edge.source);
            const target = lookup.get(edge.target);
            if (!source || !target) {
                continue;
            }
            let dx = source.x - target.x;
            let dy = source.y - target.y;
            let distance = Math.sqrt(dx * dx + dy * dy);
            if (distance < 1e-6) {
                dx = (Math.random() - 0.5) * 0.1;
                dy = (Math.random() - 0.5) * 0.1;
                distance = Math.sqrt(dx * dx + dy * dy) || 0.01;
            }
            const desired = baseLength / edge.strength;
            const displacement = distance - desired;
            const force = displacement * edge.strength;
            const fx = (dx / distance) * force;
            const fy = (dy / distance) * force;
            source.vx -= fx;
            source.vy -= fy;
            target.vx += fx;
            target.vy += fy;
        }

        simulationNodes.forEach((node) => {
            node.vx *= 0.4;
            node.vy *= 0.4;
            const displacement = Math.sqrt(node.vx * node.vx + node.vy * node.vy);
            if (displacement > temperature && displacement > 0) {
                const scale = temperature / displacement;
                node.vx *= scale;
                node.vy *= scale;
            }
            node.x += node.vx;
            node.y += node.vy;
        });

        temperature *= 0.92;
    }

    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;

    simulationNodes.forEach((node) => {
        if (node.x < minX) {
            minX = node.x;
        }
        if (node.x > maxX) {
            maxX = node.x;
        }
        if (node.y < minY) {
            minY = node.y;
        }
        if (node.y > maxY) {
            maxY = node.y;
        }
    });

    if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minY) || !Number.isFinite(maxY)) {
        return simulationNodes.map(({ vx: _vx, vy: _vy, ...rest }) => ({
            ...rest,
            x: width / 2,
            y: height / 2,
        }));
    }

    const padding = Math.max(40, Math.min(width, height) * 0.08);
    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);
    const usableWidth = Math.max(width - padding * 2, 1);
    const usableHeight = Math.max(height - padding * 2, 1);

    return simulationNodes.map(({ vx: _vx, vy: _vy, ...rest }) => ({
        ...rest,
        x: padding + ((rest.x - minX) / spanX) * usableWidth,
        y: padding + ((rest.y - minY) / spanY) * usableHeight,
    }));
};

const RelationshipGraphPanel: React.FC<RelationshipGraphPanelProps> = ({
    experimentId,
    visible,
    agents,
    onNodeSelect,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [graphPayload, setGraphPayload] = useState<RelationshipEdgesResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [viewport, setViewport] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
    const [layoutNodes, setLayoutNodes] = useState<LayoutNode[]>([]);
    const [layoutEdges, setLayoutEdges] = useState<LayoutEdge[]>([]);
    const [transform, setTransform] = useState({ scale: 1, translateX: 0, translateY: 0 });
    const aliasLookupRef = useRef<Map<string, string>>(new Map());
    const edgeKeysRef = useRef<Set<string>>(new Set());
    const highlightedRef = useRef<Set<string>>(new Set());
    const highlightTimeoutsRef = useRef<Map<string, number>>(new Map());
    const previousPositionsRef = useRef<Map<string, { x: number; y: number }>>(new Map());
    const { t } = useTranslation('replay');

    useEffect(() => {
        const element = containerRef.current;
        if (!element) {
            return;
        }
        const updateSize = () => {
            const rect = element.getBoundingClientRect();
            setViewport((prev) => {
                if (prev.width === rect.width && prev.height === rect.height) {
                    return prev;
                }
                return { width: rect.width, height: rect.height };
            });
        };
        updateSize();
        const resizeObserver = typeof ResizeObserver !== 'undefined'
            ? new ResizeObserver(() => updateSize())
            : null;
        resizeObserver?.observe(element);
        window.addEventListener('resize', updateSize);
        return () => {
            resizeObserver?.disconnect();
            window.removeEventListener('resize', updateSize);
        };
    }, []);

    useEffect(() => {
        highlightedRef.current.clear();
        highlightTimeoutsRef.current.forEach((handle) => window.clearTimeout(handle));
        highlightTimeoutsRef.current.clear();
        previousPositionsRef.current.clear();
        setTransform({ scale: 1, translateX: 0, translateY: 0 });
    }, [experimentId]);

    useEffect(() => {
        if (!experimentId) {
            setGraphPayload(null);
            setError(null);
            return;
        }
        let cancelled = false;
        const controller = new AbortController();
        const loadEdges = async () => {
            setLoading(true);
            setError(null);
            try {
                const response = await fetchCustom(
                    `/api/experiments/${experimentId}/relationship-edges`,
                    { signal: controller.signal },
                );
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const payload = (await response.json()) as RelationshipEdgesResponse;
                if (!cancelled) {
                    setGraphPayload(payload);
                }
            } catch (err) {
                if (cancelled) {
                    return;
                }
                if (err instanceof DOMException && err.name === 'AbortError') {
                    return;
                }
                console.error('Failed to load relationship edges', err);
                setGraphPayload(null);
                setError(err instanceof Error ? err.message : String(err));
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        };
        void loadEdges();
        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [experimentId]);

    useEffect(() => () => {
        highlightTimeoutsRef.current.forEach((handle) => window.clearTimeout(handle));
        highlightTimeoutsRef.current.clear();
    }, []);

    const graphData = useMemo(() => {
        const aliasLookup = new Map<string, string>();
        const layoutLookup = new Map<string, { x: number; y: number }>();
        const nodesByCanonical = new Map<string, GraphNodeInput>();
        const edges: GraphEdgeInput[] = [];

        const registerAlias = (canonical: string, token?: string | null) => {
            if (!token) {
                return;
            }
            aliasLookup.set(token, canonical);
        };

        const recordLayout = (token: string | undefined, x?: number, y?: number) => {
            if (!token) {
                return;
            }
            if (typeof x !== 'number' || typeof y !== 'number') {
                return;
            }
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return;
            }
            layoutLookup.set(token, { x, y });
        };

        if (graphPayload?.layout && typeof graphPayload.layout === 'object') {
            Object.entries(graphPayload.layout).forEach(([key, value]) => {
                const x = parseNumeric((value as Record<string, unknown>).x);
                const y = parseNumeric((value as Record<string, unknown>).y);
                if (x !== undefined && y !== undefined) {
                    recordLayout(key, x, y);
                }
            });
        }

        const upsertNode = (canonical: string, patch: Partial<GraphNodeInput>) => {
            const existing = nodesByCanonical.get(canonical);
            const next: GraphNodeInput = {
                id: canonical,
                label: patch.label ?? existing?.label ?? canonical,
                agentId: patch.agentId ?? existing?.agentId,
                sentiment: patch.sentiment ?? existing?.sentiment,
                color: patch.color ?? existing?.color ?? '#CBD5E1',
                size: patch.size ?? existing?.size ?? DEFAULT_NODE_SIZE,
                layoutX: patch.layoutX ?? existing?.layoutX,
                layoutY: patch.layoutY ?? existing?.layoutY,
            };
            nodesByCanonical.set(canonical, next);
        };

        (graphPayload?.nodes ?? []).forEach((node) => {
            const idToken = normaliseToken(node.id);
            const nameToken = normaliseToken(node.name);
            const canonical = idToken ?? nameToken;
            if (!canonical) {
                return;
            }
            const layoutX = parseNumeric(node.x);
            const layoutY = parseNumeric(node.y);
            if (layoutX !== undefined && layoutY !== undefined) {
                recordLayout(canonical, layoutX, layoutY);
                recordLayout(idToken, layoutX, layoutY);
                recordLayout(nameToken, layoutX, layoutY);
            }
            const label = (() => {
                const raw = typeof node.label === 'string' ? node.label.trim() : '';
                if (raw) {
                    return raw;
                }
                return nameToken ?? idToken ?? canonical;
            })();
            const color = (() => {
                const raw = typeof node.color === 'string' ? node.color.trim() : '';
                return raw || '#CBD5E1';
            })();
            const size = parseNumeric(node.size) ?? DEFAULT_NODE_SIZE;
            const layoutEntry = layoutLookup.get(canonical) || layoutLookup.get(idToken ?? '') || layoutLookup.get(nameToken ?? '');
            upsertNode(canonical, {
                label,
                color,
                size,
                layoutX: layoutEntry?.x ?? layoutX,
                layoutY: layoutEntry?.y ?? layoutY,
            });
            registerAlias(canonical, canonical);
            registerAlias(canonical, idToken ?? undefined);
            registerAlias(canonical, nameToken ?? undefined);
            registerAlias(canonical, label);
        });

        agents.forEach((agent) => {
            const canonical = agent.id != null ? String(agent.id) : normaliseToken(agent.name);
            if (!canonical) {
                return;
            }
            const sentiment = resolveSentiment(agent.status);
            const color = sentimentToColour(sentiment);
            const nameToken = agent.name ? String(agent.name).trim() : undefined;
            const layoutEntry = layoutLookup.get(canonical) || layoutLookup.get(nameToken ?? '');
            upsertNode(canonical, {
                agentId: agent.id,
                sentiment,
                color,
                label: nameToken || canonical,
                size: Math.max(nodesByCanonical.get(canonical)?.size ?? DEFAULT_NODE_SIZE, DEFAULT_NODE_SIZE),
                layoutX: nodesByCanonical.get(canonical)?.layoutX ?? layoutEntry?.x,
                layoutY: nodesByCanonical.get(canonical)?.layoutY ?? layoutEntry?.y,
            });
            registerAlias(canonical, canonical);
            registerAlias(canonical, nameToken ?? undefined);
            if (agent.id != null) {
                registerAlias(canonical, String(agent.id));
            }
        });

        (graphPayload?.edges ?? []).forEach((edge) => {
            const rawSource = normaliseToken(edge.source);
            const rawTarget = normaliseToken(edge.target);
            if (!rawSource || !rawTarget) {
                return;
            }
            const source = aliasLookup.get(rawSource) ?? rawSource;
            const target = aliasLookup.get(rawTarget) ?? rawTarget;
            if (source === target) {
                return;
            }
            const xs = parseCoordinateArray(edge.xs);
            const ys = parseCoordinateArray(edge.ys);
            const strength = clampStrength(normaliseStrength(edge.strength));
            edges.push({
                source,
                target,
                strength,
                xs,
                ys,
            });
            registerAlias(source, rawSource);
            registerAlias(target, rawTarget);
        });

        aliasLookupRef.current = aliasLookup;
        edgeKeysRef.current = new Set(edges.map((edge) => makeEdgeKey(edge.source, edge.target)));

        return {
            nodes: Array.from(nodesByCanonical.values()),
            edges,
        };
    }, [agents, graphPayload]);

    useEffect(() => {
        if (!visible) {
            return;
        }
        const { width, height } = viewport;
        if (width <= 0 || height <= 0) {
            return;
        }

        const hasBackendLayout = graphData.nodes.some(
            (node) => typeof node.layoutX === 'number' && typeof node.layoutY === 'number',
        );
        const hasEdgeGeometry = graphData.edges.some(
            (edge) => (edge.xs?.length ?? 0) >= 2 && (edge.ys?.length ?? 0) >= 2,
        );

        if (!hasBackendLayout && !hasEdgeGeometry) {
            const positioned = runForceLayout(
                graphData.nodes,
                graphData.edges,
                width,
                height,
                previousPositionsRef.current,
            );
            previousPositionsRef.current = new Map(
                positioned.map((node) => [node.id, { x: node.x, y: node.y }]),
            );
            const positionLookup = new Map<string, LayoutNode>();
            positioned.forEach((node) => {
                positionLookup.set(node.id, node);
            });
            const edgesWithGeometry: LayoutEdge[] = graphData.edges.map((edge) => {
                const source = positionLookup.get(edge.source);
                const target = positionLookup.get(edge.target);
                if (!source || !target) {
                    return undefined;
                }
                const points = [
                    { x: source.x, y: source.y },
                    { x: target.x, y: target.y },
                ];
                return {
                    key: makeEdgeKey(edge.source, edge.target),
                    source: edge.source,
                    target: edge.target,
                    strength: edge.strength,
                    points,
                    baseWidth: 1.2 + edge.strength * 2.4,
                    baseOpacity: Math.min(0.85, 0.35 + edge.strength * 0.35),
                };
            }).filter((edge): edge is LayoutEdge => edge !== undefined);
            setLayoutNodes(positioned);
            setLayoutEdges(edgesWithGeometry);
            return;
        }

        let minX = Number.POSITIVE_INFINITY;
        let maxX = Number.NEGATIVE_INFINITY;
        let minY = Number.POSITIVE_INFINITY;
        let maxY = Number.NEGATIVE_INFINITY;

        const includePoint = (x?: number, y?: number) => {
            if (typeof x !== 'number' || typeof y !== 'number') {
                return;
            }
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return;
            }
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        };

        graphData.nodes.forEach((node) => includePoint(node.layoutX, node.layoutY));
        graphData.edges.forEach((edge) => {
            const xs = edge.xs;
            const ys = edge.ys;
            if (!xs || !ys) {
                return;
            }
            const limit = Math.min(xs.length, ys.length);
            for (let index = 0; index < limit; index += 1) {
                includePoint(xs[index], ys[index]);
            }
        });

        if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minY) || !Number.isFinite(maxY)) {
            const positioned = runForceLayout(
                graphData.nodes,
                graphData.edges,
                width,
                height,
                previousPositionsRef.current,
            );
            previousPositionsRef.current = new Map(
                positioned.map((node) => [node.id, { x: node.x, y: node.y }]),
            );
            const positionLookup = new Map<string, LayoutNode>();
            positioned.forEach((node) => positionLookup.set(node.id, node));
            const edgesWithGeometry: LayoutEdge[] = graphData.edges.map((edge) => {
                const source = positionLookup.get(edge.source);
                const target = positionLookup.get(edge.target);
                if (!source || !target) {
                    return undefined;
                }
                const points = [
                    { x: source.x, y: source.y },
                    { x: target.x, y: target.y },
                ];
                return {
                    key: makeEdgeKey(edge.source, edge.target),
                    source: edge.source,
                    target: edge.target,
                    strength: edge.strength,
                    points,
                    baseWidth: 1.2 + edge.strength * 2.4,
                    baseOpacity: Math.min(0.85, 0.35 + edge.strength * 0.35),
                };
            }).filter((edge): edge is LayoutEdge => edge !== undefined);
            setLayoutNodes(positioned);
            setLayoutEdges(edgesWithGeometry);
            return;
        }

        const padding = Math.max(40, Math.min(width, height) * 0.08);
        const spanX = Math.max(maxX - minX, 1e-6);
        const spanY = Math.max(maxY - minY, 1e-6);
        const usableWidth = Math.max(width - padding * 2, 1);
        const usableHeight = Math.max(height - padding * 2, 1);

        const transformPoint = (x: number, y: number) => ({
            x: padding + ((x - minX) / spanX) * usableWidth,
            y: padding + ((y - minY) / spanY) * usableHeight,
        });

        const inferredPositions = new Map<string, { x: number; y: number }>();

        const positioned = graphData.nodes.map((node) => {
            if (typeof node.layoutX === 'number' && typeof node.layoutY === 'number') {
                const point = transformPoint(node.layoutX, node.layoutY);
                inferredPositions.set(node.id, point);
                return {
                    ...node,
                    x: point.x,
                    y: point.y,
                };
            }
            const inferred = inferredPositions.get(node.id);
            if (inferred) {
                return {
                    ...node,
                    x: inferred.x,
                    y: inferred.y,
                };
            }
            const previous = previousPositionsRef.current.get(node.id);
            return {
                ...node,
                x: previous?.x ?? width / 2,
                y: previous?.y ?? height / 2,
            };
        });

        const positionLookup = new Map<string, LayoutNode>();
        positioned.forEach((node) => positionLookup.set(node.id, node));

        const edgesWithGeometry: LayoutEdge[] = graphData.edges.map((edge) => {
            const points: { x: number; y: number }[] = [];
            if (edge.xs && edge.ys) {
                const limit = Math.min(edge.xs.length, edge.ys.length);
                for (let index = 0; index < limit; index += 1) {
                    const x = edge.xs[index];
                    const y = edge.ys[index];
                    if (typeof x === 'number' && typeof y === 'number') {
                        points.push(transformPoint(x, y));
                    }
                }
            }
            if (points.length < 2) {
                const source = positionLookup.get(edge.source);
                const target = positionLookup.get(edge.target);
                if (source && target) {
                    points.length = 0;
                    points.push({ x: source.x, y: source.y });
                    points.push({ x: target.x, y: target.y });
                }
            }
            if (points.length < 2) {
                return undefined;
            }
            const key = makeEdgeKey(edge.source, edge.target);
            return {
                key,
                source: edge.source,
                target: edge.target,
                strength: edge.strength,
                points,
                baseWidth: 1.2 + edge.strength * 2.4,
                baseOpacity: Math.min(0.85, 0.35 + edge.strength * 0.35),
            };
        }).filter((edge): edge is LayoutEdge => edge !== undefined);

        previousPositionsRef.current = new Map(
            positioned.map((node) => [node.id, { x: node.x, y: node.y }]),
        );
        setLayoutNodes(positioned);
        setLayoutEdges(edgesWithGeometry);
    }, [graphData, viewport, visible]);

    const handleWheel = useCallback((event: WheelEvent) => {
        if (!visible) {
            return;
        }
        event.preventDefault();
        const rect = containerRef.current?.getBoundingClientRect();
        if (!rect) {
            return;
        }
        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;
        const baseDelta = event.deltaY;
        const delta = event.deltaMode === DOM_DELTA_LINE
            ? baseDelta * 20
            : event.deltaMode === DOM_DELTA_PAGE
                ? baseDelta * rect.height
                : baseDelta;
        const zoomIntensity = 0.00085;
        const zoomFactor = Math.exp(-delta * zoomIntensity);
        setTransform((prev) => {
            const nextScale = Math.min(4.5, Math.max(0.45, prev.scale * zoomFactor));
            const scaleRatio = nextScale / prev.scale;
            const translateX = offsetX - scaleRatio * (offsetX - prev.translateX);
            const translateY = offsetY - scaleRatio * (offsetY - prev.translateY);
            return {
                scale: nextScale,
                translateX,
                translateY,
            };
        });
    }, [visible]);

    useEffect(() => {
        const element = containerRef.current;
        if (!element) {
            return;
        }
        const block = (event: Event) => {
            if (!visible) {
                return;
            }
            event.preventDefault();
        };
        element.addEventListener('wheel', handleWheel, { passive: false });
        element.addEventListener('touchmove', block, { passive: false });
        element.addEventListener('gesturestart', block as EventListener, { passive: false });
        element.addEventListener('gesturechange', block as EventListener, { passive: false });
        element.addEventListener('gestureend', block as EventListener, { passive: false });
        return () => {
            element.removeEventListener('wheel', handleWheel as EventListener);
            element.removeEventListener('touchmove', block as EventListener);
            element.removeEventListener('gesturestart', block as EventListener);
            element.removeEventListener('gesturechange', block as EventListener);
            element.removeEventListener('gestureend', block as EventListener);
        };
    }, [handleWheel, visible]);

    const dragStateRef = useRef<{
        pointerId: number;
        originX: number;
        originY: number;
        startTranslateX: number;
        startTranslateY: number;
    } | null>(null);

    const stopDragging = useCallback((svg: SVGSVGElement) => {
        const drag = dragStateRef.current;
        if (!drag) {
            return;
        }
        try {
            svg.releasePointerCapture(drag.pointerId);
        } catch (err) {
            // ignore
        }
        dragStateRef.current = null;
    }, []);

    const handlePointerDown = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
        if (!visible || event.button !== 0) {
            return;
        }
        const target = event.target as Element | null;
        if (target && target.closest('.relationship-graph-panel__node')) {
            return;
        }
        const svg = event.currentTarget;
        svg.setPointerCapture(event.pointerId);
        dragStateRef.current = {
            pointerId: event.pointerId,
            originX: event.clientX,
            originY: event.clientY,
            startTranslateX: transform.translateX,
            startTranslateY: transform.translateY,
        };
        event.preventDefault();
    }, [transform.translateX, transform.translateY, visible]);

    const handlePointerMove = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
        const drag = dragStateRef.current;
        if (!drag) {
            return;
        }
        event.preventDefault();
        const deltaX = event.clientX - drag.originX;
        const deltaY = event.clientY - drag.originY;
        setTransform((prev) => ({
            scale: prev.scale,
            translateX: drag.startTranslateX + deltaX,
            translateY: drag.startTranslateY + deltaY,
        }));
    }, []);

    const handlePointerUp = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
        event.preventDefault();
        stopDragging(event.currentTarget);
    }, [stopDragging]);

    const handlePointerLeave = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
        if (!dragStateRef.current) {
            return;
        }
        event.preventDefault();
        stopDragging(event.currentTarget);
    }, [stopDragging]);

    const triggerHighlight = useCallback((keys: string[]) => {
        if (keys.length === 0) {
            return;
        }
        const highlighted = highlightedRef.current;
        let changed = false;
        keys.forEach((key) => {
            if (!highlighted.has(key)) {
                changed = true;
            }
            highlighted.add(key);
            const existing = highlightTimeoutsRef.current.get(key);
            if (existing !== undefined) {
                window.clearTimeout(existing);
            }
            const timeout = window.setTimeout(() => {
                highlightTimeoutsRef.current.delete(key);
                if (highlighted.delete(key)) {
                    setLayoutEdges((prev) => [...prev]);
                }
            }, HIGHLIGHT_DURATION_MS);
            highlightTimeoutsRef.current.set(key, timeout);
        });
        if (changed) {
            setLayoutEdges((prev) => [...prev]);
        }
    }, []);

    useEffect(() => {
        const handleHighlight = (event: Event) => {
            const custom = event as CustomEvent<HighlightEventDetail>;
            const detail = custom.detail;
            if (!detail) {
                return;
            }
            const lookup = aliasLookupRef.current;
            const sourceToken = normaliseToken(detail.source);
            const targetToken = normaliseToken(detail.target);
            if (!sourceToken || !targetToken) {
                return;
            }
            const source = lookup.get(sourceToken) ?? sourceToken;
            const target = lookup.get(targetToken) ?? targetToken;
            const key = makeEdgeKey(source, target);
            if (!edgeKeysRef.current.has(key)) {
                return;
            }
            triggerHighlight([key]);
        };
        window.addEventListener('relationship:highlight', handleHighlight as EventListener);
        return () => {
            window.removeEventListener('relationship:highlight', handleHighlight as EventListener);
        };
    }, [triggerHighlight]);

    const renderedEdges = useMemo(() => layoutEdges.map((edge) => {
        const highlighted = highlightedRef.current.has(edge.key);
        return {
            ...edge,
            highlighted,
            strokeWidth: highlighted ? edge.baseWidth + 1.6 : edge.baseWidth,
            strokeOpacity: highlighted ? 0.95 : edge.baseOpacity,
            strokeColor: highlighted ? '#2563EB' : '#94A3B8',
        };
    }), [layoutEdges]);

    const handleNodeClick = useCallback((node: LayoutNode) => {
        const detail = {
            id: node.agentId != null ? String(node.agentId) : node.id,
            label: node.label,
        };
        window.dispatchEvent(new CustomEvent('agentsociety:relationship-node', { detail }));
        onNodeSelect?.(detail);
    }, [onNodeSelect]);

    const statusMessage = useMemo(() => {
        if (!visible) {
            return null;
        }
        if (error) {
            return (
                <div className="relationship-graph-panel__status relationship-graph-panel__status--error">
                    {t('relationshipLayout.error')}
                </div>
            );
        }
        if (loading) {
            return (
                <div className="relationship-graph-panel__status">
                    {t('relationshipLayout.loading')}
                </div>
            );
        }
        return null;
    }, [error, loading, t, visible]);

    return (
        <div
            ref={containerRef}
            className="relationship-graph-panel"
            data-visible={visible ? 'true' : 'false'}
            style={{
                touchAction: visible ? 'none' : undefined,
                overscrollBehavior: visible ? 'contain' : undefined,
                overflow: 'hidden',
                WebkitUserSelect: visible ? 'none' : undefined,
                userSelect: visible ? 'none' : undefined,
            }}
            onPointerDownCapture={(event) => {
                if (!visible) {
                    return;
                }
                const target = event.target as Element | null;
                if (target && target.closest('.relationship-graph-panel__node')) {
                    return;
                }
                event.preventDefault();
            }}
        >
            {visible && (
                <svg
                    className="relationship-graph-panel__svg"
                    width={viewport.width}
                    height={viewport.height}
                    viewBox={`0 0 ${Math.max(viewport.width, 1)} ${Math.max(viewport.height, 1)}`}
                    onPointerDown={handlePointerDown}
                    onPointerMove={handlePointerMove}
                    onPointerUp={handlePointerUp}
                    onPointerCancel={handlePointerUp}
                    onPointerLeave={handlePointerLeave}
                >
                    <g transform={`translate(${transform.translateX} ${transform.translateY}) scale(${transform.scale})`}>
                        <g className="relationship-graph-panel__edges">
                            {renderedEdges.map((edge) => (
                                <polyline
                                    key={edge.key}
                                    points={edge.points.map((point) => `${point.x},${point.y}`).join(' ')}
                                    fill="none"
                                    stroke={edge.strokeColor}
                                    strokeWidth={edge.strokeWidth}
                                    strokeOpacity={edge.strokeOpacity}
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    vectorEffect="non-scaling-stroke"
                                    strokeDasharray={edge.highlighted ? '4 4' : undefined}
                                    data-highlighted={edge.highlighted ? 'true' : 'false'}
                                />
                            ))}
                        </g>
                        <g>
                            {layoutNodes.map((node) => {
                                const radius = node.size / 2;
                                return (
                                    <g
                                        key={node.id}
                                        className="relationship-graph-panel__node"
                                        transform={`translate(${node.x}, ${node.y})`}
                                        onClick={() => handleNodeClick(node)}
                                        style={{ cursor: 'pointer' }}
                                    >
                                        <circle
                                            r={radius}
                                            fill={node.color}
                                            stroke="#0F172A"
                                            strokeWidth={1.5}
                                        />
                                        <text
                                            y={radius + 14}
                                            textAnchor="middle"
                                            dominantBaseline="hanging"
                                        >
                                            {node.label}
                                        </text>
                                    </g>
                                );
                            })}
                        </g>
                    </g>
                </svg>
            )}
            {statusMessage}
        </div>
    );
};

export default RelationshipGraphPanel;