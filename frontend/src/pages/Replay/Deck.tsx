import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import DeckGL, { type DeckGLRef } from '@deck.gl/react';
import { FlyToInterpolator, MapView, MapViewState, type Color } from '@deck.gl/core';
import { HeatmapLayer, TextLayer, IconLayer, ScatterplotLayer } from 'deck.gl';
import { Map as MapGL } from 'react-map-gl';
import tinycolor from "tinycolor2";
import { Agent } from './components/type';
import 'mapbox-gl/dist/mapbox-gl.css';
import { observer } from 'mobx-react-lite';
import { StoreContext } from './store';
import RelationshipGraphPanel from './RelationshipGraphPanel';
import { useTranslation } from 'react-i18next';

// Set your mapbox access token here
const MAPBOX_ACCESS_TOKEN = 'pk.eyJ1IjoiZmh5ZHJhbGlzayIsImEiOiJja3VzMWc5NXkwb3RnMm5sbnVvd3IydGY0In0.FrwFkYIMpLbU83K9rHSe8w';
const MAP_STYLE = 'mapbox://styles/mapbox/standard';

const AOI_COLOR_MAP = new Map<string, string>([
    ['LAND_USE_TYPE_UNSPECIFIED', '#5C8D71'],
    ['LAND_USE_TYPE_COMMERCIAL', '#5B1697'],
    ['LAND_USE_TYPE_INDUSTRIAL', '#82470C'],
    ['LAND_USE_TYPE_RESIDENTIAL', '#fffe00'],
    ['LAND_USE_TYPE_PUBLIC', '#EC3022'],
    ['LAND_USE_TYPE_TRANSPORTATION', '#979B9A'],
    ['LAND_USE_TYPE_OTHER', '#5C8D71'],
]);

const LAND_USE_NAME = new Map<string, string>([
    ['LAND_USE_TYPE_UNSPECIFIED', '未指定'],
    ['LAND_USE_TYPE_COMMERCIAL', '商服用地'],
    ['LAND_USE_TYPE_INDUSTRIAL', '工矿仓储用地'],
    ['LAND_USE_TYPE_RESIDENTIAL', '住宅用地'],
    ['LAND_USE_TYPE_PUBLIC', '公共管理与公共服务用地'],
    ['LAND_USE_TYPE_TRANSPORTATION', '交通运输用地'],
    ['LAND_USE_TYPE_OTHER', '其他土地'],
]);


interface AgentAnchor {
    key: string;
    canonical: string;
    x: number;
    y: number;
    id?: string;
    name?: string;
}

const Deck = observer((props: {
    style: React.CSSProperties,
}) => {
    const store = useContext(StoreContext)

    const { t } = useTranslation('replay');
    const containerRef = useRef<HTMLDivElement>(null);
    const deckRef = useRef<DeckGLRef>(null);
    const [curZoom, setCurZoom] = useState(10.5);
    const [hovering, setHovering] = useState(false);
    const [anchors, setAnchors] = useState<AgentAnchor[]>([]);
    const anchorsRef = useRef<AgentAnchor[]>([]);
    const frameRef = useRef<number>();
    const previousViewStateRef = useRef<MapViewState | null>(null);

    const [layoutMode, setLayoutMode] = useState<'alphabetical' | 'force'>('alphabetical');
    const showForceToggle = !store.hasMap;

    let layers: any[] = [];
    const showBaseMap = store.hasMap;

    // const aoiLayers = props.showAoi ? [new GeoJsonLayer({
    //     id: 'aoi',
    //     data: HTTPBackendUrl + `/experiments/${store.expID}/geojson/aoi`,
    //     loadOptions: {
    //         fetch: {
    //             mode: 'cors',
    //             credentials: 'include',
    //         },
    //     },
    //     pickable: true,
    //     stroked: false, // 控制所有元素是否描边
    //     filled: true, // 控制Polygon是否填充
    //     // extruded: true, // 控制Polygon是否向Z轴拉伸，形成立体效果，高度来自getElevation
    //     // getElevation: 10,
    //     getFillColor: f => {
    //         const hex = AOI_COLOR_MAP.get(f.properties!.land_use) ?? '#5C8D71';
    //         const rgba = tinycolor(hex).setAlpha(0.4).toRgb();
    //         return [rgba.r, rgba.g, rgba.b, rgba.a * 255];
    //     },
    // })] : [];

    const allAgentsList = useMemo(() => Array.from(store.agents.values()), [store.agents]);

    const agentList = useMemo<Agent[]>(() => {
        const list = Array.from(store.agents.values()).filter((agent) =>
            Number.isFinite(agent.lng) && Number.isFinite(agent.lat)
        );
        list.sort((a, b) => {
            const aKey = a.id != null ? String(a.id) : (a.name ?? "");
            const bKey = b.id != null ? String(b.id) : (b.name ?? "");
            return aKey.localeCompare(bKey);
        });
        return list;
    }, [store.agents, store.agents.size]);

    const mapCenter = store.mapCenter;
    const initialViewState = useMemo<MapViewState>(() => ({
        longitude: mapCenter.lng,
        latitude: mapCenter.lat,
        zoom: 10.5,
        pitch: 0,
        bearing: 0,
    }), [mapCenter.lng, mapCenter.lat]);

    const [deckViewState, setDeckViewState] = useState<MapViewState>(initialViewState);

    useEffect(() => {
        setDeckViewState(initialViewState);
    }, [initialViewState]);

    useEffect(() => {
        setLayoutMode('alphabetical');
        anchorsRef.current = [];
        setAnchors([]);
        setDeckViewState(initialViewState);
        previousViewStateRef.current = null;
    }, [store.expID, initialViewState]);

    useEffect(() => {
        if (!showForceToggle && layoutMode === 'force') {
            setLayoutMode('alphabetical');
        }
    }, [showForceToggle, layoutMode]);

    const positionedAgents = agentList;

    const handleToggleLayout = useCallback(() => {
        if (!showForceToggle) {
            return;
        }
        setLayoutMode((prev) => {
            const next = prev === 'force' ? 'alphabetical' : 'force';
            if (next === 'force') {
                previousViewStateRef.current = { ...deckViewState };
            } else {
                const previous = previousViewStateRef.current;
                if (previous) {
                    setDeckViewState(previous);
                }
                previousViewStateRef.current = null;
            }
            return next;
        });
    }, [showForceToggle, deckViewState]);

    const layoutButtonLabel = layoutMode === 'force'
        ? t('relationshipLayout.resetLayout')
        : t('relationshipLayout.toggleGraph');

    const scheduleAnchorUpdate = useCallback(() => {
        if (layoutMode === 'force') {
            if (anchorsRef.current.length !== 0) {
                anchorsRef.current = [];
                setAnchors([]);
            }
            return;
        }
        if (frameRef.current !== undefined) {
            return;
        }
        frameRef.current = window.requestAnimationFrame(() => {
            frameRef.current = undefined;
            const deckInstance = deckRef.current?.deck;
            const container = containerRef.current;
            if (!deckInstance || !container) {
                if (anchorsRef.current.length !== 0) {
                    anchorsRef.current = [];
                    setAnchors([]);
                }
                return;
            }
            const viewports = deckInstance.getViewports();
            const viewport = viewports && viewports[0];
            if (!viewport) {
                return;
            }

            const nextAnchors: AgentAnchor[] = [];
            for (const agent of positionedAgents) {
                const projected = viewport.project([agent.lng, agent.lat]);
                const [x, y] = projected;
                if (!Number.isFinite(x) || !Number.isFinite(y)) {
                    continue;
                }
                const canonical = agent.id != null
                    ? String(agent.id)
                    : (agent.name ?? "");
                if (!canonical) {
                    continue;
                }
                nextAnchors.push({
                    key: canonical,
                    canonical,
                    x,
                    y,
                    id: agent.id != null ? String(agent.id) : undefined,
                    name: agent.name || undefined,
                });
            }
            nextAnchors.sort((a, b) => a.key.localeCompare(b.key));

            const prevAnchors = anchorsRef.current;
            let changed = prevAnchors.length !== nextAnchors.length;
            if (!changed) {
                for (let i = 0; i < prevAnchors.length; i += 1) {
                    const prev = prevAnchors[i];
                    const next = nextAnchors[i];
                    if (prev.key !== next.key) {
                        changed = true;
                        break;
                    }
                    if (Math.abs(prev.x - next.x) > 0.5 || Math.abs(prev.y - next.y) > 0.5) {
                        changed = true;
                        break;
                    }
                    if (prev.id !== next.id || prev.name !== next.name) {
                        changed = true;
                        break;
                    }
                }
            }

            if (changed) {
                anchorsRef.current = nextAnchors;
                setAnchors(nextAnchors);
            }
        });
    }, [layoutMode, positionedAgents]);

    const handleForceNodeSelect = useCallback(({ id, label }: { id?: string; label?: string }) => {
        const candidates: string[] = [];
        if (id) {
            candidates.push(id);
        }
        if (label) {
            candidates.push(label);
        }
        for (const candidate of candidates) {
            if (candidate === undefined || candidate === null) {
                continue;
            }
            const normalized = String(candidate).trim();
            if (!normalized) {
                continue;
            }
            const numeric = Number(normalized);
            if (!Number.isNaN(numeric) && store.agents.has(numeric)) {
                void store.setClickedAgentID(numeric);
                return;
            }
            for (const agent of store.agents.values()) {
                if (String(agent.id) === normalized || (agent.name && String(agent.name) === normalized)) {
                    void store.setClickedAgentID(agent.id);
                    return;
                }
            }
        }
    }, [store]);

    useEffect(() => {
        scheduleAnchorUpdate();
    }, [scheduleAnchorUpdate]);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) {
            return;
        }
        scheduleAnchorUpdate();
        let resizeObserver: ResizeObserver | null = null;
        if (typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(() => {
                scheduleAnchorUpdate();
            });
            resizeObserver.observe(container);
        }

        const handleWindowResize = () => {
            scheduleAnchorUpdate();
        };
        window.addEventListener('resize', handleWindowResize);

        return () => {
            window.removeEventListener('resize', handleWindowResize);
            resizeObserver?.disconnect();
        };
    }, [scheduleAnchorUpdate]);

    useEffect(() => {
        return () => {
            if (frameRef.current !== undefined) {
                window.cancelAnimationFrame(frameRef.current);
            }
        };
    }, []);

    const getSentiment = (status: any): number | undefined => {
        if (status === null || status === undefined) {
            return undefined;
        }
        if (typeof status === 'number') {
            return status;
        }
        if (typeof status === 'string') {
            const num = Number(status);
            if (!Number.isNaN(num)) {
                return num;
            }
            try {
                return getSentiment(JSON.parse(status));
            } catch (err) {
                console.error('failed to parse status sentiment', err);
                return undefined;
            }
        }
        if (typeof status === 'object') {
            if ('sentiment' in status) {
                return getSentiment((status as any).sentiment);
            }
            if ('status' in status) {
                return getSentiment((status as any).status);
            }
        }
        return undefined;
    };

    const getSentimentColor = (sentiment?: number): Color => {
        const neutral: Color = [0, 255, 0, 255];
        if (typeof sentiment !== 'number' || Number.isNaN(sentiment)) {
            return neutral;
        }
        if (sentiment >= 0.2) {
            return [0, 0, 255, 255];
        }
        if (sentiment <= -0.2) {
            return [255, 0, 0, 255];
        }
        return neutral;
    };

    if (curZoom > 10) {
        const iconLayer = new IconLayer({
            id: 'icon',
            data: positionedAgents.map((a) => {
                const profile = a.profile;
                let avatarUrl = '/icon/agent.png';
                try {
                    if (profile !== undefined) {
                        const gender = profile.gender;
                        const age = profile.age;
                        if (gender === 'male' && typeof age === 'number') {
                            if (age < 18) {
                                avatarUrl = '/icon/boy1.png';
                            } else if (age < 65) {
                                avatarUrl = '/icon/boy2.png';
                            } else {
                                avatarUrl = '/icon/boy3.png';
                            }
                        } else if (gender === 'female' && typeof age === 'number') {
                            if (age < 18) {
                                avatarUrl = '/icon/girl1.png';
                            } else if (age < 65) {
                                avatarUrl = '/icon/girl2.png';
                            } else {
                                avatarUrl = '/icon/girl3.png';
                            }
                        }
                    }
                } catch (e) {
                    console.error(e);
                }
                const sentiment = getSentiment(a.status);
                return {
                    id: a.id,
                    coordinate: [a.lng, a.lat],
                    avatarUrl: avatarUrl,
                    sentiment: sentiment,
                }
            }),
            pickable: true,
            getIcon: d => ({
                url: d.avatarUrl,
                width: 128,
                height: 128,
                anchorX: 64,
                anchorY: 64,
                mask: true,
            }),
            getSize: 30,
            getPosition: d => d.coordinate,
            getColor: d => getSentimentColor(d.sentiment),
            colorMode: 'replace',
        });
        // if (iconLayers.length > 0) {
        //     console.log("number of agents: ", iconLayers.length);
        // }
        layers.push(iconLayer);

        const textLayer = new TextLayer({
            id: 'text',
            data: positionedAgents.map((a) => {
                if (a.name === "") {
                    return undefined
                } else {
                    return {
                        id: a.id,
                        position: [a.lng, a.lat],
                        text: a.name,
                    }
                }
            }).filter(d => d !== undefined),
            background: true,
            backgroundPadding: [4, 4, 4, 4],
            characterSet: 'auto',
            fontFamily: 'system-ui',
            getText: d => d.text,
            getPosition: d => d.position,
            getSize: 16,
            getBackgroundColor: [0, 0, 0, 128],
            getColor: [255, 255, 255],
            getAngle: 0,
            getPixelOffset: [0, -24],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'bottom',
            fontSettings: {
                sdf: true,
                radius: 24,
                fontSize: 128,
            },
            maxWidth: 10,
        });
        layers.push(textLayer);
    } else {
        // use point layer
        const pointLayer = new ScatterplotLayer({
            id: 'point',
            data: positionedAgents.map((a) => {
                const sentiment = getSentiment(a.status);
                return {
                    id: a.id,
                    position: [a.lng, a.lat],
                    radius: 10,
                    color: getSentimentColor(sentiment),
                }
            }),
            pickable: true,
            radiusScale: 20,
            radiusMinPixels: 1,
            radiusMaxPixels: 100,
            getPosition: d => d.position,
            getRadius: d => d.radius,
            getFillColor: d => d.color,
        });
        layers.push(pointLayer);
    }

    if (store.heatmapKeyInStatus !== undefined) {
        const heatmapLayer = new HeatmapLayer({
            id: 'heatmap',
            data: positionedAgents.map((a) => {
                return {
                    position: [a.lng, a.lat],
                    weight: a.status[store.heatmapKeyInStatus] ?? 0,
                }
            }),
            getPosition: d => d.position,
            getWeight: d => d.weight,
            threshold: 0.05,
            radiusPixels: 100,
            intensity: 1,
        });
        layers = [heatmapLayer, ...layers];
    }

    const baseContainerStyle: React.CSSProperties = {
        position: 'relative',
        width: '100%',
        height: '100%',
        ...props.style,
    };

    const containerStyle = showBaseMap
        ? baseContainerStyle
        : { ...baseContainerStyle, backgroundColor: '#ffffff' };

    return <div ref={containerRef} style={containerStyle} onContextMenu={evt => evt.preventDefault()}>
        <DeckGL
            ref={deckRef}
            initialViewState={{
                ...initialViewState,
                transitionDuration: 2000,
                transitionInterpolator: new FlyToInterpolator(),
            } as MapViewState}
            viewState={layoutMode === 'force' ? initialViewState : deckViewState}
            controller
            layers={layers}
            onViewStateChange={({ viewState }) => {
                const nextState = viewState as unknown as MapViewState;
                setCurZoom(nextState.zoom);
                if (layoutMode !== 'force') {
                    setDeckViewState(nextState);
                }
                scheduleAnchorUpdate();
            }}
            onHover={(info) => {
                const { object, coordinate } = info;
                setHovering(Boolean(object))
            }}
            getCursor={() => hovering ? 'pointer' : 'grab'}
            getTooltip={({ object, layer }) => {
                if (!object || !layer) {
                    return null;
                }
                if (layer.id === 'aoi') {
                    const name = object.properties?.name;
                    const id = object.id;
                    const landUse = LAND_USE_NAME.get(object.properties?.land_use);
                    if (name === undefined || id === undefined || landUse === undefined) {
                        return null;
                    }
                    return {
                        html: `<b>${object.properties!.name}</b><br/>ID = ${id}<br/>${landUse}`,
                        style: {
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            color: 'white',
                        }
                    }
                }
                return null;
            }}
            onClick={async (info, event) => {
                console.log(info, event);
                if (event.leftButton) {
                    const { layer, object } = info;
                    if (!layer) {
                        return;
                    }
                    if (layer.id === 'icon' || layer.id === 'point' || layer.id === 'text') {
                        const id = object.id;
                        await store.setClickedAgentID(id);
                    }
                } else {
                    // 右键弹出菜单
                    const { coordinate } = info;
                    if (coordinate) {
                        // TODO: show context menu
                    }
                }
            }}
            onAfterRender={scheduleAnchorUpdate}
            style={{
                position: 'absolute',
                inset: '0',
                opacity: layoutMode === 'force' ? '0' : '1',
                pointerEvents: layoutMode === 'force' ? 'none' : 'auto',
                transition: 'opacity 0.2s ease',
            }}
        >
            {showBaseMap && (
                /* @ts-ignore */
                <MapView id="map" width="100%" controller>
                    <MapGL mapboxAccessToken={MAPBOX_ACCESS_TOKEN} mapStyle={MAP_STYLE} />
                </MapView>
            )}
        </DeckGL>
        <RelationshipGraphPanel
            experimentId={store.expID}
            visible={showForceToggle && layoutMode === 'force'}
            agents={allAgentsList}
            onNodeSelect={handleForceNodeSelect}
        />
        {showForceToggle && (
            <div
                style={{
                    position: 'absolute',
                    top: 20,
                    left: '50%',
                    transform: 'translateX(-50%)',
                    display: 'flex',
                    zIndex: 1001,
                    pointerEvents: 'auto',
                }}
            >
                <button
                    type="button"
                    onClick={handleToggleLayout}
                    style={{
                        cursor: 'pointer',
                        padding: '8px 18px',
                        borderRadius: 9999,
                        border: '1px solid rgba(148, 163, 184, 0.5)',
                        backgroundColor: layoutMode === 'force' ? '#E2E8F0' : '#2563EB',
                        color: layoutMode === 'force' ? '#1F2937' : '#FFFFFF',
                        fontSize: 13,
                        fontWeight: 500,
                        boxShadow: '0 4px 8px rgba(15, 23, 42, 0.18)',
                        transition: 'background-color 0.2s ease, color 0.2s ease',
                        whiteSpace: 'nowrap',
                    }}
                >
                    {layoutButtonLabel}
                </button>
            </div>
        )}
        {layoutMode !== 'force' && (
            <div
                aria-hidden="true"
                style={{
                    pointerEvents: 'none',
                    position: 'absolute',
                    inset: 0,
                    zIndex: 2,
                }}
            >
                {anchors.map((anchor) => (
                    <div
                        key={anchor.key}
                        className="agent-node"
                        data-agent-id={anchor.id}
                        data-agent-name={anchor.name}
                        data-agent-canonical={anchor.canonical}
                        style={{
                            position: 'absolute',
                            left: 0,
                            top: 0,
                            width: 0,
                            height: 0,
                            transform: `translate(${anchor.x}px, ${anchor.y}px)`,
                        }}
                    />
                ))}
            </div>
        )}
    </div>;
});

export default Deck;
