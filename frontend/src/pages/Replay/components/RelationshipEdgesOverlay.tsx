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
    line_width?: unknown;
    line_alpha?: unknown;
    is_backbone?: unknown;
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

interface RelationshipEdgesOverlayProps {
    experimentId?: string;
    containerRef: React.RefObject<HTMLElement>;
    active?: boolean;
}

interface DerivedEdge {
    key: string;
    start: { x: number; y: number };
    end: { x: number; y: number };
    width: number;
    opacity: number;
    strength: number;
    isBackbone: boolean;
    isWeak: boolean;
    highlighted: boolean;
}

interface DerivedGeometry {
    edges: DerivedEdge[];
    viewBox: { minX: number; minY: number; width: number; height: number } | null;
    hasWeakEdges: boolean;
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

const parseRangeMetadata = (value: unknown): {
    start: number;
    end: number;
    min?: number;
    max?: number;
    span?: number;
} | undefined => {
    if (!value || typeof value !== 'object') {
        return undefined;
    }
    const record = value as Record<string, unknown>;
    const start = parseNumeric(record.start);
    const end = parseNumeric(record.end);
    if (start === undefined || end === undefined) {
        return undefined;
    }
    const min = parseNumeric(record.min);
    const max = parseNumeric(record.max);
    const span = parseNumeric(record.span);
    return {
        start,
        end,
        min: min === undefined ? undefined : min,
        max: max === undefined ? undefined : max,
        span: span === undefined ? undefined : span,
    };
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
    const [showWeakEdges, setShowWeakEdges] = useState(true);

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
        const handler = (event: Event) => {
            const detail = (event as CustomEvent<{ show?: boolean }>).detail;
            if (!detail || typeof detail.show !== 'boolean') {
                return;
            }
            setShowWeakEdges(detail.show);
        };
        window.addEventListener('relationship:set-show-weak-edges', handler as EventListener);
        return () => {
            window.removeEventListener('relationship:set-show-weak-edges', handler as EventListener);
        };
    }, []);

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

    const geometry = useMemo<DerivedGeometry>(() => {
        const aliasLookup = new Map<string, string>();
        const coordinateLookup = new Map<string, { x: number; y: number }>();

        if (!active || !payload) {
            aliasLookupRef.current = aliasLookup;
            edgeKeysRef.current = new Set();
            return { edges: [], viewBox: null, hasWeakEdges: false };
        }

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
                coordinateLookup.set(canonical, { x: layoutX, y: layoutY });
            }
        });

        if (payload.layout) {
            Object.entries(payload.layout).forEach(([rawKey, value]) => {
                const canonicalKey = normaliseToken(rawKey) ?? rawKey;
                aliasLookup.set(canonicalKey, canonicalKey);
                aliasLookup.set(rawKey, canonicalKey);
                const record = value as Record<string, unknown>;
                const x = parseNumeric(record?.x);
                const y = parseNumeric(record?.y);
                if (x !== undefined && y !== undefined) {
                    coordinateLookup.set(canonicalKey, { x, y });
                }
            });
        }

        type ParsedEdge = {
            source: string;
            target: string;
            strength: number;
            xs?: number[];
            ys?: number[];
            lineWidth?: number;
            lineAlpha?: number;
            isBackbone: boolean;
        };

        const edgesByKey = new Map<string, ParsedEdge>();
        const ingestEdges = (edges: RawRelationshipEdge[] | undefined, fallbackIsBackbone: boolean) => {
            (edges ?? []).forEach((edge) => {
                const rawSource = normaliseToken(edge.source);
                const rawTarget = normaliseToken(edge.target);
                if (!rawSource || !rawTarget) {
                    return;
                }
                const source = aliasLookup.get(rawSource) ?? rawSource;
                const target = aliasLookup.get(rawTarget) ?? rawTarget;
                const strength = clampStrength(parseNumeric(edge.strength));
                const xs = parseCoordinateArray(edge.xs);
                const ys = parseCoordinateArray(edge.ys);
                const lineWidth = parseNumeric(edge.line_width);
                const lineAlpha = parseNumeric(edge.line_alpha);
                const isBackbone = typeof edge.is_backbone === 'boolean'
                    ? edge.is_backbone
                    : fallbackIsBackbone;
                const key = makeEdgeKey(source, target);
                edgesByKey.set(key, {
                    source,
                    target,
                    strength,
                    xs,
                    ys,
                    lineWidth,
                    lineAlpha,
                    isBackbone,
                });
            });
        };

        ingestEdges(payload.edges_backbone, true);
        ingestEdges(payload.edges_rest, false);
        if (edgesByKey.size === 0) {
            ingestEdges(payload.edges, false);
        }

        const xRange = parseRangeMetadata(payload.x_range);
        const yRange = parseRangeMetadata(payload.y_range);

        if (!xRange || !yRange) {
            console.warn('RelationshipEdgesOverlay: missing range metadata', {
                experimentId,
            });
            aliasLookupRef.current = aliasLookup;
            edgeKeysRef.current = new Set();
            return { edges: [], viewBox: null, hasWeakEdges: false };
        }

        let hasWeakEdges = false;
        const results: DerivedEdge[] = [];
        edgesByKey.forEach((edge) => {
            let startX: number | undefined;
            let startY: number | undefined;
            let endX: number | undefined;
            let endY: number | undefined;

            if (edge.xs && edge.ys) {
                const limit = Math.min(edge.xs.length, edge.ys.length);
                if (limit >= 2) {
                    startX = edge.xs[0];
                    startY = edge.ys[0];
                    endX = edge.xs[limit - 1];
                    endY = edge.ys[limit - 1];
                }
            }

            if (
                startX === undefined
                || startY === undefined
                || endX === undefined
                || endY === undefined
            ) {
                const sourceLayout = coordinateLookup.get(edge.source);
                const targetLayout = coordinateLookup.get(edge.target);
                if (sourceLayout && targetLayout) {
                    startX = sourceLayout.x;
                    startY = sourceLayout.y;
                    endX = targetLayout.x;
                    endY = targetLayout.y;
                }
            }

            if (
                startX === undefined
                || startY === undefined
                || endX === undefined
                || endY === undefined
            ) {
                return;
            }

            if (
                !Number.isFinite(startX)
                || !Number.isFinite(startY)
                || !Number.isFinite(endX)
                || !Number.isFinite(endY)
            ) {
                return;
            }

            const baseWidth = edge.lineWidth ?? (1 + 2.5 * edge.strength);
            let width = Math.max(0.2, baseWidth);
            const baseOpacity = edge.lineAlpha ?? (0.3 + 0.6 * edge.strength);
            let opacity = Math.max(0, Math.min(0.95, baseOpacity));
            if (edge.isBackbone) {
                width = Math.max(width, 2.4);
                opacity = Math.max(opacity, 0.65);
            } else {
                width = Math.min(Math.max(width, 1.2), 1.6);
                opacity = Math.min(Math.max(opacity, 0.35), 0.5);
            }

            const isWeak = !edge.isBackbone && edge.strength < 0.3;
            if (isWeak) {
                hasWeakEdges = true;
                if (!showWeakEdges) {
                    return;
                }
            }

            results.push({
                key: makeEdgeKey(edge.source, edge.target),
                start: { x: startX, y: startY },
                end: { x: endX, y: endY },
                width,
                opacity,
                strength: edge.strength,
                isBackbone: edge.isBackbone,
                isWeak,
                highlighted: false,
            });
        });

        const sortedEdges = [...results].sort((a, b) => {
            if (a.isBackbone !== b.isBackbone) {
                return a.isBackbone ? 1 : -1;
            }
            const opacityDiff = b.opacity - a.opacity;
            if (Math.abs(opacityDiff) > 1e-6) {
                return opacityDiff;
            }
            return b.width - a.width;
        });

        aliasLookupRef.current = aliasLookup;
        edgeKeysRef.current = new Set(sortedEdges.map((edge) => edge.key));

        const width = Math.max(xRange.end - xRange.start, 1e-6);
        const height = Math.max(yRange.end - yRange.start, 1e-6);

        if (sortedEdges.length === 0) {
            console.debug('RelationshipEdgesOverlay: no edges to render', {
                experimentId,
                edgeCount: edgesByKey.size,
                ranges: { x: xRange, y: yRange },
            });
        } else {
            console.debug('RelationshipEdgesOverlay: rendering edges', {
                experimentId,
                edgeCount: sortedEdges.length,
                ranges: { x: xRange, y: yRange },
            });
        }

        return {
            edges: sortedEdges,
            viewBox: {
                minX: xRange.start,
                minY: yRange.start,
                width,
                height,
            },
            hasWeakEdges,
        };
    }, [payload, active, experimentId, showWeakEdges]);

    const derivedEdges = geometry.edges;
    const viewBox = geometry.viewBox;
    const hasWeakEdges = geometry.hasWeakEdges;

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

    if (
        !active
        || !payload
        || viewport.width <= 0
        || viewport.height <= 0
        || !viewBox
    ) {
        return null;
    }

    return (
        <>
            <svg
                className="relationship-edges-overlay"
                width={viewport.width}
                height={viewport.height}
                viewBox={`${viewBox.minX} ${viewBox.minY} ${viewBox.width} ${viewBox.height}`}
                preserveAspectRatio="xMidYMid meet" // [FIX] align overlay scaling with main graph
                style={{
                    position: 'absolute',
                    inset: 0,
                    pointerEvents: 'none',
                    zIndex: 3,
                }}
            >
                {edgesWithHighlight.map((edge) => (
                    <line
                        key={edge.key}
                        x1={edge.start.x}
                        y1={edge.start.y}
                        x2={edge.end.x}
                        y2={edge.end.y}
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
            {hasWeakEdges ? (
                <div
                    style={{
                        position: 'absolute',
                        bottom: 12,
                        right: 12,
                        pointerEvents: 'none',
                        zIndex: 2,
                    }}
                >
                    <button
                        type="button"
                        onClick={() => setShowWeakEdges((value) => !value)}
                        style={{
                            pointerEvents: 'auto',
                            padding: '4px 8px',
                            fontSize: '12px',
                            borderRadius: 6,
                            border: '1px solid rgba(148, 163, 184, 0.6)',
                            background: 'rgba(15, 23, 42, 0.65)',
                            color: '#F8FAFC',
                            cursor: 'pointer',
                        }}
                        title="Toggle display of weak relationship edges"
                    >
                        {showWeakEdges ? 'Hide weak edges' : 'Show weak edges'}
                    </button>
                </div>
            ) : null}
        </>
    );
};

export default RelationshipEdgesOverlay;