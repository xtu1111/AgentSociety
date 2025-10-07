import React, {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from 'react';
import {
    forceSimulation,
    forceLink,
    forceManyBody,
    forceCenter,
    forceCollide,
    type SimulationNodeDatum,
    type SimulationLinkDatum,
} from 'd3-force';
import { useTranslation } from 'react-i18next';
import { fetchCustom } from '../../components/fetch';
import type { Agent } from './components/type';

// 兜底：当 props.agents 不带 connections 时，我们再拉一次 profile
interface AgentWithMaybeConnections extends Agent {
    connections?: Array<{ source?: string | number; target?: string | number; strength?: number }>;
}

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
    community?: unknown;
}

interface RawRelationshipEdge {
    source?: unknown;
    target?: unknown;
    strength?: unknown;
    xs?: unknown;
    ys?: unknown;
    line_width?: unknown;
    line_alpha?: unknown;
}

interface RawRangeMetadata {
    start?: unknown;
    end?: unknown;
    min?: unknown;
    max?: unknown;
    span?: unknown;
}

interface RelationshipEdgesResponse {
    nodes?: RawRelationshipNode[];
    edges?: RawRelationshipEdge[];
    edges_backbone?: RawRelationshipEdge[];
    edges_rest?: RawRelationshipEdge[];
    layout?: Record<string, { x?: unknown; y?: unknown }>;
    x_range?: RawRangeMetadata;
    y_range?: RawRangeMetadata;
}

interface BackendNode {
    id: string;
    label: string;
    color: string;
    radius: number;
    community: number;
    x?: number;
    y?: number;
}

interface BackendEdge {
    key: string;
    source: string;
    target: string;
    strength: number;
    xs?: number[];
    ys?: number[];
    layer: 'backbone' | 'rest';
}

interface ForceNodeDatum extends SimulationNodeDatum {
    id: string;
    radius: number;
    community: number;
    x?: number;
    y?: number;
    vx?: number;
    vy?: number;
}

interface ForceLinkDatum extends SimulationLinkDatum<ForceNodeDatum> {
    source: string;
    target: string;
    strength: number;
    isInterCommunity: boolean;
}

interface RangeMetadata {
    min: number;
    max: number;
    start: number;
    end: number;
    span: number;
}

const DEFAULT_NODE_RADIUS = 18;
const MIN_STRENGTH = 0.1;
const MAX_DISTANCE = 500;
const MIN_DISTANCE = 30;

const COMMUNITY_COLOURS = [
    '#0EA5E9',
    '#F97316',
    '#10B981',
    '#8B5CF6',
    '#F43F5E',
    '#F59E0B',
    '#6366F1',
    '#22C55E',
];

const toStringId = (value: unknown): string | undefined => {
    if (value === null || value === undefined) {
        return undefined;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed === '' ? undefined : trimmed;
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
        return String(value);
    }
    return String(value);
};

const toNumber = (value: unknown): number | undefined => {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string') {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
};

// === BEGIN: alias & sentiment helpers ===
const normalizeToken = (v: unknown): string | undefined => {
    if (v === null || v === undefined) return undefined;
    if (typeof v === 'string') {
        const s = v.trim();
        return s ? s : undefined;
    }
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
    return String(v);
};

const resolveSentiment = (status: unknown): number | undefined => {
    if (status === null || status === undefined) return undefined;
    if (typeof status === 'number') return status;
    if (typeof status === 'string') {
        const n = Number(status);
        if (!Number.isNaN(n)) return n;
        try { return resolveSentiment(JSON.parse(status)); } catch { return undefined; }
    }
    if (typeof status === 'object') {
        const obj = status as Record<string, unknown>;
        if ('sentiment' in obj) return resolveSentiment(obj.sentiment);
        if ('status' in obj) return resolveSentiment(obj.status);
    }
    return undefined;
};

const sentimentToColour = (s: number | undefined): string => {
    if (typeof s !== 'number' || Number.isNaN(s)) return '#00FF00'; // 中立
    if (s >= 0.2) return '#0000FF';  // 积极
    if (s <= -0.2) return '#FF0000'; // 消极
    return '#00FF00';
};
// === END: alias & sentiment helpers ===

// 停用词过滤（避免伪节点）
const STOP_WORDS = new Set([
    'sentiment',
    'adopted',
    'emotion',
    'status',
    'status_summary',
    'message_source',
    'message_source:company',
    'message',
    'summary',
]);

const pruneIfStopWord = (id?: string): boolean =>
    !!id && STOP_WORDS.has(id.trim().toLowerCase());

const parseCoordinates = (raw: unknown): number[] | undefined => {
    if (!Array.isArray(raw)) {
        return undefined;
    }
    const coords = raw
        .map((entry) => toNumber(entry))
        .filter((entry): entry is number => entry !== undefined);
    return coords.length >= 2 ? coords : undefined;
};

const clampStrength = (strength: number | undefined): number => {
    if (typeof strength !== 'number' || Number.isNaN(strength)) {
        return MIN_STRENGTH;
    }
    if (strength < 0.05) {
        return 0.05;
    }
    if (strength > 1) {
        return 1;
    }
    return strength;
};

const getCommunityStroke = (community: number): string => {
    if (!Number.isInteger(community) || community < 0) {
        return '#475569';
    }
    return COMMUNITY_COLOURS[community % COMMUNITY_COLOURS.length];
};

const buildPath = (xs: number[], ys: number[]): string | null => {
    const limit = Math.min(xs.length, ys.length);
    if (limit < 2) {
        return null;
    }
    const path: string[] = [];
    path.push(`M ${xs[0]} ${ys[0]}`);
    if (limit === 2) {
        path.push(`L ${xs[1]} ${ys[1]}`);
    } else if (limit === 3) {
        path.push(`Q ${xs[1]} ${ys[1]} ${xs[2]} ${ys[2]}`);
    } else {
        path.push(`C ${xs[1]} ${ys[1]} ${xs[2]} ${ys[2]} ${xs[3]} ${ys[3]}`);
        for (let i = 4; i < limit; i += 1) {
            path.push(`L ${xs[i]} ${ys[i]}`);
        }
    }
    return path.join(' ');
};

const RelationshipGraphPanel: React.FC<RelationshipGraphPanelProps> = ({
    experimentId,
    visible,
    agents,
    onNodeSelect,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [payload, setPayload] = useState<RelationshipEdgesResponse | null>(null);
    const [viewport, setViewport] = useState({ width: 0, height: 0 });
    const [transform, setTransform] = useState({ scale: 1, translateX: 0, translateY: 0 });
    const [showBackboneOnly, setShowBackboneOnly] = useState(false);
    const [minStrength, setMinStrength] = useState(0.05);
    const [backboneCoverage, setBackboneCoverage] = useState(0.6);
    const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
    const [range, setRange] = useState<{ x: RangeMetadata; y: RangeMetadata } | null>(null);
    const highlightRef = useRef<Set<string>>(new Set());
    const highlightTimerRef = useRef<Map<string, number>>(new Map());
    const [highlightVersion, setHighlightVersion] = useState(0);
    const { t } = useTranslation('replay');
    // 兜底用的 agents 列表（可能自带 connections）
    const [agentsForEdges, setAgentsForEdges] = useState<AgentWithMaybeConnections[]>(
        Array.isArray(agents) ? (agents as AgentWithMaybeConnections[]) : [],
    );

    // 拖拽相关
    const draggingRef = useRef(false);
    const lastPointRef = useRef<{ x: number; y: number } | null>(null);

    // 拖拽事件
    const onPointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
        if (e.button !== 0) {
            return;
        }
        e.currentTarget.setPointerCapture?.(e.pointerId);
        draggingRef.current = true;
        lastPointRef.current = { x: e.clientX, y: e.clientY };
    }, []);

    const onPointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
        if (!draggingRef.current || !lastPointRef.current) {
            return;
        }
        const dx = e.clientX - lastPointRef.current.x;
        const dy = e.clientY - lastPointRef.current.y;
        lastPointRef.current = { x: e.clientX, y: e.clientY };
        setTransform((prev) => ({
            ...prev,
            translateX: prev.translateX + dx / prev.scale,
            translateY: prev.translateY + dy / prev.scale,
        }));
    }, []);

    const onPointerUp = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
        draggingRef.current = false;
        lastPointRef.current = null;
    }, []);

    const onPointerCancel = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
        draggingRef.current = false;
        lastPointRef.current = null;
    }, []);

    const onPointerLeave = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
        if (!draggingRef.current) {
            return;
        }
        onPointerUp(e);
    }, [onPointerUp]);

    useEffect(() => {
        const element = containerRef.current;
        if (!element) {
            return;
        }
        const handleResize = () => {
            const rect = element.getBoundingClientRect();
            setViewport({ width: rect.width, height: rect.height });
        };
        handleResize();
        const observer = new ResizeObserver(() => handleResize());
        observer.observe(element);
        window.addEventListener('resize', handleResize);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', handleResize);
        };
    }, []);

    useEffect(() => {
        const element = containerRef.current;
        if (!element) {
            return;
        }
        const handler = (event: WheelEvent) => {
            if ((event.target as Element | null)?.closest('svg')) {
                event.preventDefault();
            }
        };
        element.addEventListener('wheel', handler, { passive: false, capture: true });
        return () => {
            element.removeEventListener('wheel', handler, { capture: true });
        };
    }, []);

    useEffect(() => {
        highlightRef.current.clear();
        highlightTimerRef.current.forEach((handle) => window.clearTimeout(handle));
        highlightTimerRef.current.clear();
        setTransform({ scale: 1, translateX: 0, translateY: 0 });
        setShowBackboneOnly(false);
        setMinStrength(0.05);
        setBackboneCoverage(0.6);
        setPositions(new Map());
        // 同步外部 agents
        setAgentsForEdges(
            Array.isArray(agents) ? (agents as AgentWithMaybeConnections[]) : [],
        );
    }, [experimentId, agents]);

    // 如果当前 agents 没有 connections，自动兜底拉取一份（仅在需要时发起）
    useEffect(() => {
        const hasAnyConnections =
            Array.isArray(agentsForEdges)
            && agentsForEdges.some(
                (agent) => Array.isArray((agent as AgentWithMaybeConnections).connections)
                    && ((agent as AgentWithMaybeConnections).connections?.length ?? 0) > 0,
            );
        if (!experimentId || hasAnyConnections) {
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const res = await fetchCustom(`/api/experiments/${experimentId}/agents/-/profile`);
                if (!res.ok) {
                    return;
                }
                const data = await res.json();
                if (!cancelled && Array.isArray(data)) {
                    setAgentsForEdges(data as AgentWithMaybeConnections[]);
                }
            } catch {
                // 忽略兜底失败
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [experimentId, agentsForEdges]);

    useEffect(() => {
        if (!experimentId) {
            setPayload(null);
            setError(null);
            return;
        }
        let cancelled = false;
        const controller = new AbortController();
        const fetchEdges = async () => {
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
                const json = (await response.json()) as RelationshipEdgesResponse;
                // [DEBUG] relationship-edges payload counters
                try {
                    console.debug('[relationship-edges]', {
                        nodes: Array.isArray((json as any)?.nodes) ? (json as any).nodes.length : undefined,
                        edges: Array.isArray((json as any)?.edges) ? (json as any).edges.length : undefined,
                        edges_backbone: Array.isArray((json as any)?.edges_backbone)
                            ? (json as any).edges_backbone.length
                            : undefined,
                        edges_rest: Array.isArray((json as any)?.edges_rest)
                            ? (json as any).edges_rest.length
                            : undefined,
                        agentsForEdges_hasConnections:
                            Array.isArray(agentsForEdges)
                                && agentsForEdges.some(
                                    (agent) => Array.isArray((agent as any).connections)
                                        && ((agent as any).connections?.length ?? 0) > 0,
                                ),
                    });
                } catch (_) {
                    // noop
                }
                if (!cancelled) {
                    setPayload(json);
                }
            } catch (err) {
                if (cancelled) {
                    return;
                }
                if (err instanceof DOMException && err.name === 'AbortError') {
                    return;
                }
                console.error('Failed to load relationship graph payload', err);
                setError(err instanceof Error ? err.message : String(err));
                setPayload(null);
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        };
        void fetchEdges();
        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [experimentId, agentsForEdges]);

    const parsedData = useMemo(() => {
        if (!payload) return null;

        // 别名表：raw token -> canonical id
        const alias = new Map<string, string>();
        const nodesById = new Map<string, BackendNode>();

        const registerAlias = (canonical: string, ...tokens: Array<string | undefined>) => {
            tokens.forEach((t) => { if (t) alias.set(t, canonical); });
        };

        // 1) 先放入后端节点，并记录别名；坐标优先用 layout 覆盖
        (payload.nodes ?? []).forEach((raw) => {
            const idTok = normalizeToken(raw.id);
            const nameTok = normalizeToken(raw.name);
            const labelRaw = typeof raw.label === 'string' ? raw.label.trim() : '';
            const canonical = idTok ?? nameTok ?? labelRaw;
            if (!canonical) return;

            const label = labelRaw || nameTok || idTok || canonical;
            const community = Number.isFinite(raw.community as number) ? Number(raw.community) : 0;
            const size = toNumber(raw.size) ?? DEFAULT_NODE_RADIUS;
            const defaultColor = typeof raw.color === 'string' && raw.color ? raw.color : '#CBD5F5';

            const layout = payload.layout ?? {};
            const lx = layout[canonical]?.x ?? layout[idTok ?? '']?.x ?? layout[nameTok ?? '']?.x;
            const ly = layout[canonical]?.y ?? layout[idTok ?? '']?.y ?? layout[nameTok ?? '']?.y;

            nodesById.set(canonical, {
                id: canonical,
                label,
                color: defaultColor,          // 先占位，稍后由 agents 覆盖
                radius: size,
                community,
                x: toNumber(lx),
                y: toNumber(ly),
            });

            registerAlias(canonical, canonical, idTok, nameTok, label);
        });

        // 2) 用 agents 覆盖/补充节点，颜色来自 sentiment，确保与默认视图同步
        agentsForEdges.forEach((a) => {
            const idTok = a.id != null ? String(a.id) : undefined;
            const nameTok = a.name ? String(a.name).trim() : undefined;
            const canonical = idTok ?? nameTok;
            if (!canonical) return;

            const existing = nodesById.get(canonical);
            const sentiment = resolveSentiment(a.status);
            const color = sentimentToColour(sentiment);

            nodesById.set(canonical, {
                id: canonical,
                label: nameTok || idTok || canonical,
                color,
                radius: existing?.radius ?? DEFAULT_NODE_RADIUS,
                community: existing?.community ?? 0,
                x: existing?.x,
                y: existing?.y,
            });

            registerAlias(canonical, canonical, idTok, nameTok);
        });

        for (const id of Array.from(nodesById.keys())) {
            if (pruneIfStopWord(id)) {
                nodesById.delete(id);
            }
        }

        for (const [token, canonical] of Array.from(alias.entries())) {
            if (pruneIfStopWord(canonical)) {
                alias.delete(token);
            }
        }

        const nodeIdSet = new Set<string>(Array.from(nodesById.keys()));

        // 3) 解析边：source/target 通过别名解析到 canonical id
        const edges: BackendEdge[] = [];
        const ingest = (raw: RawRelationshipEdge, layer: 'backbone' | 'rest') => {
            const sTok = normalizeToken(raw.source);
            const tTok = normalizeToken(raw.target);
            if (!sTok || !tTok) return;

            // 先做别名解析；如果别名里没有，就用原 token 作为 canonical
            const source = alias.get(sTok) ?? sTok;
            const target = alias.get(tTok) ?? tTok;
            if (pruneIfStopWord(source) || pruneIfStopWord(target)) {
                return;
            }
            if (source === target) return;

            // 如果端点缺失，就现场补节点（避免把边丢掉 → 圆环 fallback）
            const ensureNode = (canonical: string) => {
                if (pruneIfStopWord(canonical)) {
                    return;
                }
                if (nodesById.has(canonical)) return;

                // 颜色优先跟随 agents（按 name 或 id 匹配），匹配不到就用中立绿
                const matchedAgent = agentsForEdges.find(
                    (a) => String(a.id) === canonical || (a.name && a.name.trim() === canonical),
                );

                const s = resolveSentiment(matchedAgent?.status);
                const color = sentimentToColour(s);

                // 尝试从 layout 里拿初始坐标
                const lx = payload.layout?.[canonical]?.x;
                const ly = payload.layout?.[canonical]?.y;

                nodesById.set(canonical, {
                    id: canonical,
                    label: matchedAgent?.name?.trim() || canonical,
                    color,
                    radius: DEFAULT_NODE_RADIUS,
                    community: 0,
                    x: toNumber(lx),
                    y: toNumber(ly),
                });

                nodeIdSet.add(canonical);
                // 自反别名，后续能继续解析到它
                alias.set(canonical, canonical);
            };

            ensureNode(source);
            ensureNode(target);

            const strength = clampStrength(toNumber(raw.strength));
            edges.push({
                key: [source, target].sort().join('::'),
                source,
                target,
                strength,
                xs: parseCoordinates(raw.xs),
                ys: parseCoordinates(raw.ys),
                layer,
            });
        };

        // --- 正确的边源选择 & 回退 ---
        const bbSrc = (Array.isArray(payload.edges_backbone) && payload.edges_backbone.length > 0)
            ? payload.edges_backbone
            : [];

        const restSrc = (Array.isArray(payload.edges_rest) && payload.edges_rest.length > 0)
            ? payload.edges_rest
            : [];

        if (bbSrc.length === 0 && restSrc.length === 0 && Array.isArray(payload.edges) && payload.edges.length > 0) {
            payload.edges.forEach((e: any) => ingest(e, e?.is_backbone ? 'backbone' : 'rest'));
        } else {
            bbSrc.forEach((e) => ingest(e, 'backbone'));
            restSrc.forEach((e) => ingest(e, 'rest'));
        }

        // === [FALLBACK] 如果接口没有任何边，从 agentsForEdges[].connections 兜底生成 ===
        if (edges.length === 0 && Array.isArray(agentsForEdges)) {
            const ensureNode = (canonical: string) => {
                if (pruneIfStopWord(canonical) || nodesById.has(canonical)) return;

                const matchedAgent = agentsForEdges.find(
                    (a) => String(a.id) === canonical || (a.name && a.name.trim() === canonical),
                );
                const s = resolveSentiment(matchedAgent?.status);
                const color = sentimentToColour(s);
                const lx = payload.layout?.[canonical]?.x;
                const ly = payload.layout?.[canonical]?.y;

                nodesById.set(canonical, {
                    id: canonical,
                    label: matchedAgent?.name?.trim() || canonical,
                    color,
                    radius: DEFAULT_NODE_RADIUS,
                    community: 0,
                    x: toNumber(lx),
                    y: toNumber(ly),
                });

                alias.set(canonical, canonical);
            };

            agentsForEdges.forEach((a) => {
                const agentCanonical = normalizeToken(a.id) ?? normalizeToken(a.name);
                const conns: any[] | undefined = (a as any)?.connections;
                if (!Array.isArray(conns)) return;

                conns.forEach((c) => {
                    if (!c) return;
                    const sTok = normalizeToken(c.source) ?? agentCanonical;
                    const tTok = normalizeToken(c.target);
                    if (!tTok || !sTok) return;

                    const src = alias.get(sTok) ?? sTok;
                    const tgt = alias.get(tTok) ?? tTok;
                    if (pruneIfStopWord(src) || pruneIfStopWord(tgt) || src === tgt) return;

                    ensureNode(src);
                    ensureNode(tgt);

                    const strength = clampStrength(toNumber(c.strength));
                    edges.push({
                        key: [src, tgt].sort().join('::'),
                        source: src,
                        target: tgt,
                        strength,
                        layer: 'rest',
                    });
                });
            });

            try {
                console.debug('[relationship-edges:fallback-from-agents]', {
                    used: edges.length > 0,
                    edgeCount: edges.length,
                });
            } catch (_) {}
        }

        return { nodes: Array.from(nodesById.values()), edges };
    }, [payload, agentsForEdges]);

    useEffect(() => {
        if (!parsedData || parsedData.nodes.length === 0) {
            setPositions(new Map());
            return;
        }

        const covered = parsedData.nodes.filter((node) => Number.isFinite(node.x) && Number.isFinite(node.y)).length;
        const coverage = covered / parsedData.nodes.length;

        if (coverage >= 0.8) {
            const direct = new Map<string, { x: number; y: number }>();
            parsedData.nodes.forEach((node) => {
                if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
                    direct.set(node.id, { x: node.x!, y: node.y! });
                }
            });

            const missing = parsedData.nodes.filter((node) => !direct.has(node.id));
            if (missing.length > 0) {
                const existingCoords = Array.from(direct.values());
                const centroid = existingCoords.reduce(
                    (acc, coord) => ({ x: acc.x + coord.x, y: acc.y + coord.y }),
                    { x: 0, y: 0 },
                );
                if (existingCoords.length > 0) {
                    centroid.x /= existingCoords.length;
                    centroid.y /= existingCoords.length;
                }
                const radius = 120 + missing.length * 10;
                missing.forEach((node, index) => {
                    const angle = (index / missing.length) * Math.PI * 2;
                    direct.set(node.id, {
                        x: centroid.x + Math.cos(angle) * radius,
                        y: centroid.y + Math.sin(angle) * radius,
                    });
                });
            }

            setPositions(direct);
            return;
        }

        const nodesMap = new Map(parsedData.nodes.map((node) => [node.id, node]));
        const forceNodes: ForceNodeDatum[] = parsedData.nodes.map((node) => ({
            id: node.id,
            radius: node.radius,
            community: node.community,
            x: node.x,
            y: node.y,
        }));

        if (!parsedData.edges.length) {
            const fallback = new Map<string, { x: number; y: number }>();
            const cx = viewport.width / 2 || 0;
            const cy = viewport.height / 2 || 0;
            const phi = (Math.sqrt(5) - 1) / 2;
            const dtheta = 2 * Math.PI * phi;
            const step = 22;

            parsedData.nodes.forEach((node, index) => {
                const r = step * Math.sqrt(index + 1);
                const theta = index * dtheta;
                fallback.set(node.id, {
                    x: cx + r * Math.cos(theta),
                    y: cy + r * Math.sin(theta),
                });
            });

            setPositions(fallback);
            return;
        }

        const links: ForceLinkDatum[] = parsedData.edges.map((edge) => ({
            source: edge.source,
            target: edge.target,
            strength: edge.strength,
            isInterCommunity:
                (nodesMap.get(edge.source)?.community ?? 0)
                !== (nodesMap.get(edge.target)?.community ?? 0),
        }));

        const distanceStrict = (strength: number) => {
            const clamped = Math.min(1, Math.max(0.05, strength));
            return MIN_DISTANCE + Math.pow(1 - clamped, 1.4) * (MAX_DISTANCE - MIN_DISTANCE);
        };

        const sim = forceSimulation(forceNodes)
            .force(
                'link',
                forceLink<ForceNodeDatum, ForceLinkDatum>(links)
                    .id((node) => node.id)
                    .distance((edge) => distanceStrict(edge.strength))
                    .strength(() => 0.9),
            )
            .force('charge', forceManyBody().strength(-80))
            .force('collide', forceCollide<ForceNodeDatum>().radius((node) => node.radius + 6))
            .force('center', forceCenter(0, 0))
            .stop();

        const iterations = Math.max(900, forceNodes.length * 30);
        for (let i = 0; i < iterations; i += 1) {
            sim.tick();
        }

        const latest = new Map<string, { x: number; y: number }>();
        forceNodes.forEach((node) => {
            latest.set(node.id, {
                x: node.x ?? 0,
                y: node.y ?? 0,
            });
        });
        setPositions(latest);
    }, [parsedData, viewport.width, viewport.height]);

    useEffect(() => {
        if (!parsedData) {
            setRange(null);
            return;
        }
        if (payload?.x_range && payload?.y_range) {
            const safeNum = (value: unknown, fallback: number) =>
                typeof value === 'number' && Number.isFinite(value) ? value : fallback;
            const xr = payload.x_range;
            const yr = payload.y_range;
            const xStart = safeNum(xr.start, 0);
            const xEnd = safeNum(xr.end, 1200);
            const yStart = safeNum(yr.start, 0);
            const yEnd = safeNum(yr.end, 1200);
            setRange({
                x: {
                    start: xStart,
                    end: xEnd,
                    min: safeNum(xr.min, xStart),
                    max: safeNum(xr.max, xEnd),
                    span: safeNum(xr.span, Math.max(xEnd - xStart, 1)),
                },
                y: {
                    start: yStart,
                    end: yEnd,
                    min: safeNum(yr.min, yStart),
                    max: safeNum(yr.max, yEnd),
                    span: safeNum(yr.span, Math.max(yEnd - yStart, 1)),
                },
            });
            return;
        }
        const coords = Array.from(positions.values());
        if (coords.length === 0) {
            const xs = parsedData.nodes.map((node) => node.x ?? 0);
            const ys = parsedData.nodes.map((node) => node.y ?? 0);
            const minX = Math.min(...xs, -200);
            const maxX = Math.max(...xs, 200);
            const minY = Math.min(...ys, -200);
            const maxY = Math.max(...ys, 200);
            const pad = 50;
            setRange({
                x: { start: minX - pad, end: maxX + pad, min: minX, max: maxX, span: (maxX + pad) - (minX - pad) },
                y: { start: minY - pad, end: maxY + pad, min: minY, max: maxY, span: (maxY + pad) - (minY - pad) },
            });
            return;
        }
        const xs = coords.map((coord) => coord.x);
        const ys = coords.map((coord) => coord.y);
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const pad = 80;
        setRange({
            x: {
                start: minX - pad,
                end: maxX + pad,
                min: minX,
                max: maxX,
                span: maxX - minX + pad * 2,
            },
            y: {
                start: minY - pad,
                end: maxY + pad,
                min: minY,
                max: maxY,
                span: maxY - minY + pad * 2,
            },
        });
    }, [parsedData, positions, payload]);

    const derivedEdges = useMemo(() => {
        if (!parsedData) {
            return { backbone: [], rest: [] } as { backbone: BackendEdge[]; rest: BackendEdge[] };
        }
        const sorted = [...parsedData.edges].sort((a, b) => b.strength - a.strength);
        const ensureCoverage = Math.max(backboneCoverage, 0.5);
        const limit = Math.max(Math.floor(sorted.length * ensureCoverage), Math.min(sorted.length, 30));
        const thresholdStrength = sorted[limit - 1]?.strength ?? 0;

        const backbone: BackendEdge[] = [];
        const rest: BackendEdge[] = [];
        parsedData.edges.forEach((edge) => {
            if (edge.layer === 'backbone') {
                backbone.push(edge);
            } else if (edge.strength >= thresholdStrength) {
                rest.push(edge);
            }
        });
        return { backbone, rest };
    }, [parsedData, backboneCoverage]);

    const nodesForRender = useMemo(() => {
        if (!parsedData) {
            return [] as BackendNode[];
        }
        return parsedData.nodes.map((node) => {
            const coord = positions.get(node.id);
            return {
                ...node,
                x: coord?.x ?? node.x ?? 0,
                y: coord?.y ?? node.y ?? 0,
            };
        });
    }, [parsedData, positions]);

    const buildEdgePath = useCallback((edge: BackendEdge) => {
        if (edge.xs && edge.ys && edge.xs.length >= 2 && edge.ys.length >= 2) {
            return buildPath(edge.xs, edge.ys);
        }
        const source = nodesForRender.find((node) => node.id === edge.source);
        const target = nodesForRender.find((node) => node.id === edge.target);
        if (!source || !target) {
            return null;
        }
        return buildPath([source.x, target.x], [source.y, target.y]);
    }, [nodesForRender]);

    const filteredEdges = useMemo(() => {
        const applyStrengthFilter = (edge: BackendEdge) => edge.strength >= minStrength;
        return {
            backbone: derivedEdges.backbone.filter(applyStrengthFilter),
            rest: showBackboneOnly
                ? []
                : derivedEdges.rest.filter(applyStrengthFilter),
        };
    }, [derivedEdges, minStrength, showBackboneOnly]);

    const handleWheel = useCallback(
        (event: React.WheelEvent<SVGSVGElement>) => {
            const deltaMode = event.deltaMode;
            let deltaY = event.deltaY;
            const DOM_DELTA_LINE = 1;
            const DOM_DELTA_PAGE = 2;
            if (deltaMode === DOM_DELTA_LINE) {
                deltaY *= 40;
            } else if (deltaMode === DOM_DELTA_PAGE) {
                deltaY *= 800;
            }

            if (deltaY === 0) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();

            const SCROLL_PIXELS_PER_STEP = 120;
            const SCALE_STEP = 1.1;
            const exponent = -deltaY / SCROLL_PIXELS_PER_STEP;
            const baseFactor = Math.exp(Math.log(SCALE_STEP) * exponent);
            const pinchBoost = event.ctrlKey ? 1.06 : 1.0;
            const scaleFactor = baseFactor * pinchBoost;
            const { clientX, clientY, currentTarget } = event;
            const rect = currentTarget.getBoundingClientRect();
            const hasValidRect = rect.width > 0 && rect.height > 0;
            const svgX =
                range && hasValidRect
                    ? range.x.start + ((clientX - rect.left) / rect.width) * range.x.span
                    : 0;
            const svgY =
                range && hasValidRect
                    ? range.y.start + ((clientY - rect.top) / rect.height) * range.y.span
                    : 0;

            setTransform((prev) => {
                const unclamped = prev.scale * scaleFactor;
                const nextScale = Math.min(4, Math.max(0.4, unclamped));

                if (!range || !hasValidRect) {
                    if (nextScale === prev.scale) {
                        return prev;
                    }
                    return { ...prev, scale: nextScale };
                }

                if (nextScale === prev.scale) {
                    return prev;
                }

                const worldX = svgX / prev.scale - prev.translateX;
                const worldY = svgY / prev.scale - prev.translateY;
                const nextTranslateX = svgX / nextScale - worldX;
                const nextTranslateY = svgY / nextScale - worldY;

                return {
                    scale: nextScale,
                    translateX: nextTranslateX,
                    translateY: nextTranslateY,
                };
            });
        },
        [range],
    );

    const handleReset = useCallback(() => {
        setTransform({ scale: 1, translateX: 0, translateY: 0 });
        setPositions((prev) => new Map(prev));
    }, []);

    const viewBox = useMemo(() => {
        if (!range) {
            return '0 0 1 1';
        }
        return `${range.x.start} ${range.y.start} ${range.x.span} ${range.y.span}`;
    }, [range]);

    const renderEdge = (edge: BackendEdge, highlighted: boolean) => {
        const path = buildEdgePath(edge);
        if (!path) {
            return null;
        }
        const isBackbone = edge.layer === 'backbone';
        const width = isBackbone
            ? 1 + 4 * edge.strength
            : 0.5 + 2 * edge.strength;
        const opacity = isBackbone
            ? 0.3 + 0.6 * edge.strength
            : 0.1 + 0.4 * edge.strength;
        return (
            <path
                key={edge.key}
                d={path}
                stroke={highlighted ? '#2563EB' : '#94A3B8'}
                strokeWidth={highlighted ? width + 1 : width}
                strokeOpacity={Math.min(0.95, opacity)}
                fill="none"
                strokeDasharray={highlighted ? '6 4' : undefined}
                vectorEffect="non-scaling-stroke"
            />
        );
    };

    const renderNode = (node: BackendNode) => (
        <g key={node.id} transform={`translate(${node.x ?? 0}, ${node.y ?? 0})`}>
            <circle
                r={node.radius}
                fill={node.color}
                stroke="none"
                strokeWidth={0}
            />
            <text
                x={node.radius + 4}
                y={4}
                fontSize={12}
                fill="#0F172A"
                pointerEvents="none"
            >
                {node.label}
            </text>
        </g>
    );

    const handleNodeClick = useCallback((node: BackendNode, event?: React.MouseEvent<SVGGElement>) => {
        event?.stopPropagation();
        try {
            if (typeof window !== 'undefined') {
                const detail = { id: node.id, label: node.label };
                window.dispatchEvent(
                    new CustomEvent('agentsociety:relationship-node', { detail, bubbles: false }),
                );
                window.dispatchEvent(
                    new CustomEvent('replay:relationship-node', { detail, bubbles: false }),
                );
            }
        } catch {
            // ignore dispatch failure
        }
        onNodeSelect?.({ id: node.id, label: node.label });
    }, [onNodeSelect]);

    const isHighlighted = useCallback((edge: BackendEdge) => highlightRef.current.has(edge.key), []);

    if (!visible) {
        return null;
    }

    return (
        <div
            className="relationship-graph-panel"
            ref={containerRef}
            style={{
                position: 'relative',
                width: '100%',
                height: '100%',
                overscrollBehavior: 'none',
                touchAction: 'none',
            }}
            onWheelCapture={(event) => {
                const target = event.target as Element | null;
                if (target?.closest('[data-role="relationship-toolbar"]')) {
                    event.preventDefault();
                    event.stopPropagation();
                }
            }}
        >
            <div
                data-role="relationship-toolbar"
                style={{
                    position: 'absolute',
                    top: 12,
                    left: 12,
                    display: 'flex',
                    gap: 12,
                    alignItems: 'center',
                    zIndex: 10000,
                    pointerEvents: 'auto',
                }}
                onPointerDownCapture={(event) => event.stopPropagation()}
                onPointerMoveCapture={(event) => event.stopPropagation()}
                onPointerUpCapture={(event) => event.stopPropagation()}
                onWheelCapture={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                }}
            >
                <label style={{ display: 'flex', flexDirection: 'column', color: '#0F172A', fontSize: 12 }}>
                    {t('relationshipGraph.minStrength', 'Min strength')}
                    <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={minStrength}
                        onChange={(event) => setMinStrength(Number(event.target.value))}
                        style={{ width: 120 }}
                    />
                    <span>{minStrength.toFixed(2)}</span>
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', color: '#0F172A', fontSize: 12 }}>
                    {t('relationshipGraph.backboneOnly', 'Backbone only')}
                    <input
                        type="checkbox"
                        checked={showBackboneOnly}
                        onChange={(event) => setShowBackboneOnly(event.target.checked)}
                    />
                </label>
                <button type="button" onClick={handleReset} style={{ padding: '4px 8px' }}>
                    {t('relationshipGraph.resetLayout', 'Reset view')}
                </button>
            </div>

            {error && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#DC2626' }}>
                    {error}
                </div>
            )}

            <svg
                width="100%"
                height="100%"
                viewBox={viewBox}
                preserveAspectRatio="xMidYMid meet"
                onWheel={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    handleWheel(event);
                }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerLeave}
                onPointerCancel={onPointerCancel}
                style={{
                    position: 'absolute',
                    inset: 0,
                    zIndex: 1,
                    background: '#F8FAFC',
                    touchAction: 'none',
                    pointerEvents: 'auto',
                    cursor: draggingRef.current ? 'grabbing' : 'grab',
                }}
            >
                <g transform={`translate(${transform.translateX}, ${transform.translateY}) scale(${transform.scale})`}>
                    {filteredEdges.rest.map((edge) => renderEdge(edge, isHighlighted(edge)))}
                    {filteredEdges.backbone.map((edge) => renderEdge(edge, isHighlighted(edge)))}
                    {nodesForRender.map((node) => (
                        <g
                            key={node.id}
                            transform={`translate(${node.x ?? 0}, ${node.y ?? 0})`}
                            onClick={(event) => handleNodeClick(node, event)}
                            style={{ cursor: 'pointer' }}
                        >
                            <circle
                                r={node.radius}
                                fill={node.color}
                                stroke="none"
                                strokeWidth={0}
                            />
                            <text
                                x={node.radius + 4}
                                y={4}
                                fontSize={12}
                                fill="#0F172A"
                                pointerEvents="none"
                            >
                                {node.label}
                            </text>
                        </g>
                    ))}
                </g>
            </svg>
        </div>
    );
};

export default RelationshipGraphPanel;