import React, {
    useEffect,
    useMemo,
    useRef,
    useState,
} from 'react';

import { fetchCustom } from '../../../components/fetch';

interface RawRelationshipNode {
    id?: unknown;
    name?: unknown;
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

interface RelationshipEdgesOverlayProps {
    experimentId?: string;
    containerRef: React.RefObject<HTMLElement>;
    active?: boolean;
}

interface DerivedEdge {
    key: string;
    points: { x: number; y: number }[];
    width: number;
    opacity: number;
    highlighted: boolean;
}

interface HighlightEventDetail {
    source?: string | number;
    target?: string | number;
}

const BASE_STROKE_COLOR = '#94A3B8';
const HIGHLIGHT_COLOR = '#2563EB';
const HIGHLIGHT_DURATION_MS = 600;

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
        return 0.4;
    }
    if (value < 0.1) {
        return 0.1;
    }
    if (value > 1) {
        return 1;
    }
    return value;
};

const makeEdgeKey = (a: string, b: string): string => [a, b].sort().join('::');

const RelationshipEdgesOverlay: React.FC<RelationshipEdgesOverlayProps> = ({
    experimentId,
    containerRef,
    active = true,
}) => {
    const [payload, setPayload] = useState<RelationshipEdgesResponse | null>(null);
    const [viewport, setViewport] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
    const highlightedEdges = useRef<Map<string, number>>(new Map());
    const edgeKeysRef = useRef<Set<string>>(new Set());
    const aliasLookupRef = useRef<Map<string, string>>(new Map());
    const [version, setVersion] = useState(0);

    useEffect(() => {
        const element = containerRef.current;
        if (!element) {
            setViewport({ width: 0, height: 0 });
            return;
        }
        const updateSize = () => {
            const rect = element.getBoundingClientRect();
            setViewport({ width: rect.width, height: rect.height });
        };
        updateSize();
        const resizeObserver = typeof ResizeObserver !== 'undefined'
            ? new ResizeObserver(updateSize)
            : null;
        resizeObserver?.observe(element);
        window.addEventListener('resize', updateSize);
        return () => {
            resizeObserver?.disconnect();
            window.removeEventListener('resize', updateSize);
        };
    }, [containerRef]);

    useEffect(() => {
        if (!active || !experimentId) {
            setPayload(null);
            return;
        }
        let cancelled = false;
        const controller = new AbortController();
        const load = async () => {
            try {
                const response = await fetchCustom(
                    `/api/experiments/${experimentId}/relationship-edges`,
                    { signal: controller.signal },
                );
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const json = (await response.json()) as RelationshipEdgesResponse;
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
                console.error('Failed to load relationship edges', err);
                setPayload(null);
            }
        };
        void load();
        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [experimentId, active]);

    useEffect(() => {
        if (!active) {
            highlightedEdges.current.clear();
            return;
        }
        highlightedEdges.current.clear();
        setVersion((v) => v + 1);
    }, [payload, active]);

    useEffect(() => {
        if (!active) {
            return undefined;
        }
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
            const timestamp = Date.now();
            highlightedEdges.current.set(key, timestamp);
            setVersion((v) => v + 1);
            window.setTimeout(() => {
                if (highlightedEdges.current.get(key) === timestamp) {
                    highlightedEdges.current.delete(key);
                    setVersion((v) => v + 1);
                }
            }, HIGHLIGHT_DURATION_MS);
        };
        window.addEventListener('relationship:highlight', handleHighlight as EventListener);
        return () => {
            window.removeEventListener('relationship:highlight', handleHighlight as EventListener);
        };
    }, [active]);

    const derivedEdges = useMemo(() => {
        if (!active || !payload || viewport.width <= 0 || viewport.height <= 0) {
            return [] as DerivedEdge[];
        }

        const aliasLookup = new Map<string, string>();
        const layoutLookup = new Map<string, { x: number; y: number }>();

        (payload.nodes ?? []).forEach((node) => {
            const idToken = normaliseToken(node.id);
            const nameToken = normaliseToken(node.name);
            const canonical = idToken ?? nameToken;
            if (!canonical) {
                return;
            }
            aliasLookup.set(canonical, canonical);
            if (idToken) {
                aliasLookup.set(idToken, canonical);
            }
            if (nameToken) {
                aliasLookup.set(nameToken, canonical);
            }
            const layoutX = parseNumeric(node.x);
            const layoutY = parseNumeric(node.y);
            if (layoutX !== undefined && layoutY !== undefined) {
                layoutLookup.set(canonical, { x: layoutX, y: layoutY });
            }
        });

        if (payload.layout) {
            Object.entries(payload.layout).forEach(([key, value]) => {
                const x = parseNumeric((value as Record<string, unknown>).x);
                const y = parseNumeric((value as Record<string, unknown>).y);
                if (x !== undefined && y !== undefined) {
                    layoutLookup.set(key, { x, y });
                }
            });
        }

        const edges = (payload.edges ?? []).flatMap((edge) => {
            const rawSource = normaliseToken(edge.source);
            const rawTarget = normaliseToken(edge.target);
            if (!rawSource || !rawTarget) {
                return [] as const;
            }
            const source = aliasLookup.get(rawSource) ?? rawSource;
            const target = aliasLookup.get(rawTarget) ?? rawTarget;
            const strength = clampStrength(parseNumeric(edge.strength));
            const xs = parseCoordinateArray(edge.xs);
            const ys = parseCoordinateArray(edge.ys);
            return [{ source, target, strength, xs, ys }];
        });

        aliasLookupRef.current = aliasLookup;
        edgeKeysRef.current = new Set(edges.map((edge) => makeEdgeKey(edge.source, edge.target)));

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

        layoutLookup.forEach((point) => includePoint(point.x, point.y));
        edges.forEach((edge) => {
            if (!edge.xs || !edge.ys) {
                return;
            }
            const limit = Math.min(edge.xs.length, edge.ys.length);
            if (limit >= 2) {
                includePoint(edge.xs[0], edge.ys[0]);
                includePoint(edge.xs[limit - 1], edge.ys[limit - 1]);
            }
        });

        const hasGeometry = Number.isFinite(minX) && Number.isFinite(maxX) && Number.isFinite(minY) && Number.isFinite(maxY);
        if (!hasGeometry) {
            return [] as DerivedEdge[];
        }

        const padding = Math.max(40, Math.min(viewport.width, viewport.height) * 0.08);
        const spanX = Math.max(maxX - minX, 1e-6);
        const spanY = Math.max(maxY - minY, 1e-6);
        const usableWidth = Math.max(viewport.width - padding * 2, 1);
        const usableHeight = Math.max(viewport.height - padding * 2, 1);

        const transformPoint = (x: number, y: number) => ({
            x: padding + ((x - minX) / spanX) * usableWidth,
            y: padding + ((y - minY) / spanY) * usableHeight,
        });

        const results: DerivedEdge[] = [];

        edges.forEach((edge) => {
            const key = makeEdgeKey(edge.source, edge.target);
            const width = 1 + 2.5 * edge.strength;
            const opacity = Math.max(0, Math.min(0.9, 0.3 + 0.6 * edge.strength));
            const points: { x: number; y: number }[] = [];

            if (edge.xs && edge.ys) {
                const limit = Math.min(edge.xs.length, edge.ys.length);
                if (limit >= 2) {
                    const firstX = edge.xs[0];
                    const firstY = edge.ys[0];
                    const lastX = edge.xs[limit - 1];
                    const lastY = edge.ys[limit - 1];
                    if (typeof firstX === 'number' && typeof firstY === 'number') {
                        points.push(transformPoint(firstX, firstY));
                    }
                    if (typeof lastX === 'number' && typeof lastY === 'number') {
                        points.push(transformPoint(lastX, lastY));
                    }
                }
            }

            if (points.length < 2) {
                const sourceLayout = layoutLookup.get(edge.source);
                const targetLayout = layoutLookup.get(edge.target);
                if (sourceLayout && targetLayout) {
                    points.push(transformPoint(sourceLayout.x, sourceLayout.y));
                    points.push(transformPoint(targetLayout.x, targetLayout.y));
                }
            }

            if (points.length >= 2) {
                results.push({
                    key,
                    points,
                    width,
                    opacity,
                    highlighted: false,
                });
            }
        });

        if (results.length === 0) {
            console.debug('RelationshipEdgesOverlay: no edges to render', {
                experimentId,
                edgeCount: edges.length,
            });
        } else {
            console.debug('RelationshipEdgesOverlay: rendering edges', {
                experimentId,
                edgeCount: results.length,
            });
        }

        return results;
    }, [payload, viewport, active]);

    const edgesWithHighlight = useMemo(() => derivedEdges.map((edge) => {
        const highlighted = highlightedEdges.current.has(edge.key);
        return {
            ...edge,
            highlighted,
            stroke: highlighted ? HIGHLIGHT_COLOR : BASE_STROKE_COLOR,
            strokeOpacity: highlighted
                ? Math.min(0.95, edge.opacity + 0.12)
                : edge.opacity,
            strokeWidth: highlighted ? edge.width + 1 : edge.width,
        };
    }), [derivedEdges, version]);

    if (!active || !payload || viewport.width <= 0 || viewport.height <= 0) {
        return null;
    }

    return (
        <svg
            className="relationship-edges-overlay"
            width={viewport.width}
            height={viewport.height}
            viewBox={`0 0 ${Math.max(viewport.width, 1)} ${Math.max(viewport.height, 1)}`}
            style={{
                position: 'absolute',
                inset: 0,
                pointerEvents: 'none',
                zIndex: 3,
            }}
        >
            {edgesWithHighlight.map((edge) => (
                <polyline
                    key={edge.key}
                    points={edge.points.map((point) => `${point.x},${point.y}`).join(' ')}
                    fill="none"
                    stroke={edge.stroke}
                    strokeWidth={edge.strokeWidth}
                    strokeOpacity={edge.strokeOpacity}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    vectorEffect="non-scaling-stroke"
                    strokeDasharray={edge.highlighted ? '6 4' : undefined}
                />
            ))}
        </svg>
    );
};

export default RelationshipEdgesOverlay;
