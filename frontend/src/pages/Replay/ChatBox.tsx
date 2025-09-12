import { Button, Flex, GetProp, message, Modal, Row, Select, Tabs } from 'antd';
import { AndroidOutlined, ArrowUpOutlined, CommentOutlined, ProfileOutlined, SmileOutlined, UpOutlined, UserOutlined, LineChartOutlined } from '@ant-design/icons';
import { AgentDialog, ApiMetric } from './components/type';
import { Bubble, Sender } from '@ant-design/x';
import { parseT } from '../../components/util';
import React, { useContext, useState } from 'react';
import { observer } from 'mobx-react-lite';
import { StoreContext } from './store';
import { Model, Survey as SurveyUI } from 'survey-react-ui';
import { fetchCustom } from '../../components/fetch';
import { useTranslation } from 'react-i18next';
import Plot from 'react-plotly.js';
import { type PlotData, type PlotType } from 'plotly.js';

const roles: GetProp<typeof Bubble.List, 'roles'> = {
    self: {
        placement: 'start',
        avatar: { icon: <UserOutlined />, style: { background: '#fde3cf' } },
        style: {
            maxWidth: 600,
        },
    },
    otherAgent: {
        placement: 'end',
        avatar: { icon: <UserOutlined />, style: { background: '#cce3cf' } },
        style: {
            maxWidth: 600,
        },
    },
    user: {
        placement: 'end',
        avatar: { icon: <UserOutlined />, style: { background: '#87d068' } },
    },
};

export const RightPanel = observer(() => {
    const store = useContext(StoreContext);
    const { t } = useTranslation();

    const agent = store.clickedAgent;
    const agentDialogs = store.clickedAgentDialogs;
    const agentSurveys = store.clickedAgentSurveys;

    const [content, setContent] = useState<string>('');
    const [openPreview, setOpenPreview] = useState(false);
    const [previewSurvey, setPreviousSurvey] = useState<string>();

    // 生成Select选项
    const selectOptions = Array.from(store.id2surveys.values()).map(item => ({
        value: item.id,
        label: item.name,
    }));

    // 状态管理
    const [selectedSurveyID, setSelectedSurveyID] = useState<string | undefined>(undefined);

    // 处理选择变化
    const handleSelectChange = (value: string) => {
        setSelectedSurveyID(value);
    };

    // 提交操作
    const handleSelectSubmit = async () => {
        // 进行其他操作
        const res = await fetchCustom(`/api/experiments/${store.expID}/agents/${agent?.id}/survey`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                survey_id: selectedSurveyID,
                day: store.currentTime?.day ?? 0,
                t: store.currentTime?.t ?? 0,
            }),
        });
        if (res.status !== 200) {
            console.error('Failed to send survey:', res);
        } else {
            message.success(t('replay.chatbox.survey.surveySent'));
        }
        setSelectedSurveyID(undefined);
    };

    const getRoleByChatMessage = (m: AgentDialog) => {
        if (m.type === 0) {
            return { role: "self", name: agent?.name };
        }
        if (m.type === 1) {
            if (m.speaker === "" || m.speaker === agent?.id.toString()) {
                return { role: "self", name: agent?.name };
            } else {
                const otherId = parseInt(m.speaker);
                const otherAgent = store.agents.get(otherId);
                if (otherAgent === undefined) {
                    return { role: "otherAgent", name: "Unknown" };
                }
                return { role: "otherAgent", name: otherAgent.name };
            }
        }
        if (m.type === 2) {
            if (m.speaker === "") {
                return { role: "self", name: agent?.name };
            } else {
                return { role: "user", name: "User" };
            }
        }
    };

    const renderDialogs = (type: number) => {
        const dialogs = agentDialogs.filter(m => m.type === type);

        // remove duplicate messages and preserve receiver info when available
        const uniqueMap = new Map<
            string,
            AgentDialog & { receiverId?: number; receiverName?: string }
        >();
        for (const m of dialogs) {
            let content = m.content;
            let receiverId: number | undefined;
            let receiverName: string | undefined;
            try {
                const contentJson = JSON.parse(m.content);
                if (contentJson.content !== undefined) {
                    content = contentJson.content;
                }
                if (contentJson.receiver_id !== undefined) {
                    receiverId = parseInt(contentJson.receiver_id);
                }
                if (contentJson.receiver_name !== undefined) {
                    receiverName = contentJson.receiver_name;
                }
                if (contentJson.receiver !== undefined) {
                    receiverName = contentJson.receiver;
                }
            } catch {
                const match = m.content.match(
                    /^[{]\s*['"]?content['"]?\s*:\s*['"]([^'"]*)['"]/,
                );
                if (match) {
                    content = match[1];
                }
                const recIdMatch = m.content.match(
                    /['"]receiver_id['"]\s*:\s*['"]?(\d+)/,
                );
                if (recIdMatch) {
                    receiverId = parseInt(recIdMatch[1]);
                }
                const recNameMatch = m.content.match(
                    /['"]receiver_name['"]\s*:\s*['"]([^'"]+)['"]/,
                );
                if (recNameMatch) {
                    receiverName = recNameMatch[1];
                }
                const recMatch = m.content.match(
                    /['"]receiver['"]\s*:\s*['"]([^'"]+)['"]/,
                );
                if (recMatch) {
                    receiverName = recMatch[1];
                }
            }
            const key = `${m.day}-${m.t}-${m.type}-${m.speaker}-${content}`;
            const existing = uniqueMap.get(key);
            if (existing) {
                if (receiverId !== undefined && existing.receiverId === undefined) {
                    existing.receiverId = receiverId;
                }
                if (receiverName && !existing.receiverName) {
                    existing.receiverName = receiverName;
                }
                continue;
            }
            uniqueMap.set(key, { ...m, content, receiverId, receiverName });
        }
        const uniqueDialogs = Array.from(uniqueMap.values());

        // track the last other-agent speaker for displaying the receiver
        let lastOtherSpeaker: number | undefined;
        const items = uniqueDialogs.map((m, i) => {
            const { role, name } = getRoleByChatMessage(m);
            let headerName = name;
            if (m.type === 1) {
                if (m.speaker === "" || m.speaker === agent?.id.toString()) {
                    let toName =
                        m.receiverId !== undefined
                            ? store.agents.get(m.receiverId)?.name
                            : m.receiverName;
                    if (!toName) {
                        toName =
                            lastOtherSpeaker !== undefined
                                ? store.agents.get(lastOtherSpeaker)?.name
                                : undefined;
                    }
                    if (!toName) {
                        for (let j = i + 1; j < uniqueDialogs.length; j++) {
                            const next = uniqueDialogs[j];
                            if (
                                next.type === 1 &&
                                next.speaker !== "" &&
                                next.speaker !== agent?.id.toString()
                            ) {
                                toName = store.agents.get(parseInt(next.speaker))?.name;
                                break;
                            }
                        }
                    }
                    headerName = `${name} to ${toName ?? 'whom'}`;
                } else {
                    lastOtherSpeaker = parseInt(m.speaker);
                }
            }
            return {
                key: `${m.id}-${i}`,
                loading: false,
                role: role,
                content: m.content,
                header: (
                    <div>
                        {headerName} ({t('replay.day', { day: m.day })} {parseT(m.t)})
                    </div>
                ),
            };
        });

        return (
            <>
                <Bubble.List
                    roles={roles}
                    className={type === 2 ? 'bubble-input' : 'bubble-no-input'}
                    items={items}
                />
                {type === 2 && <>
                    <Sender
                        value={content}
                        onChange={setContent}
                        disabled={store.experiment?.status !== 1 || agent === undefined}
                        onSubmit={async (content: string) => {
                            if (agent === undefined) {
                                return;
                            }
                            const res = await fetchCustom(`/api/experiments/${store.expID}/agents/${agent.id}/dialog`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ content: content, day: store.currentTime?.day ?? 0, t: store.currentTime?.t ?? 0 }),
                            });
                            if (res.status !== 200) {
                                console.error('Failed to send message:', res);
                            } else {
                                message.success(t('replay.chatbox.dialog.sendSuccess'));
                            }
                            setContent('');
                        }}
                    /></>}
            </>
        );
    };

    // 将一个Survey中的提问转换为User，回答转换为Agent
    const renderSurveys = () => (
        <>
            <Bubble.List
                roles={roles}
                className='bubble-input'
                items={agentSurveys.map((s, i) => {
                    const survey = store.id2surveys.get(s.survey_id);
                    if (survey === undefined) {
                        const results = [{
                            key: `${s.id}-${i}`,
                            loading: false,
                            role: "user",
                            content: `Survey ID: ${s.survey_id} (deleted)`,
                            header: <div>Survey Day {s.day} {parseT(s.t)}</div>
                        }]
                        if (s.result) {
                            results.push({
                                key: `${s.id}-${i}-1`,
                                loading: false,
                                role: "agent",
                                content: JSON.stringify(s.result),
                                header: <div>{agent?.name} Day {s.day} {parseT(s.t)}</div>
                            })
                        }
                        return results;
                    } else {
                        const results = [{
                            key: `${s.id}-${i}`,
                            loading: false,
                            role: "user",
                            content: <div>{t('replay.chatbox.survey.surveyName')}: {survey.name} <Button
                                type='link'
                                onClick={() => {
                                    setPreviousSurvey(JSON.stringify(survey.data));
                                    setOpenPreview(true);
                                }}
                            >
                                {t('replay.chatbox.survey.preview')}
                            </Button></div>,
                            header: <div>Survey Day {s.day} {parseT(s.t)}</div>
                        }]
                        if (s.result) {
                            results.push({
                                key: `${s.id}-${i}-1`,
                                loading: false,
                                role: "agent",
                                content: <div>{Object.entries(s.result).map(([k, v]) => (
                                    <><span>{k}: {JSON.stringify(v)}</span><br /></>
                                ))}</div>,
                                header: <div>{agent?.name} Day {s.day} {parseT(s.t)}</div>
                            })
                        }
                        return results;
                    }
                }).flat()}
            />
            <Row align='middle' justify='center'>
                <Select
                    style={{ width: 'calc(100% - 64px)' }}
                    size="large"
                    showSearch
                    placement='topLeft'
                    suffixIcon={<UpOutlined />}
                    value={selectedSurveyID}
                    onChange={handleSelectChange}
                    filterOption={(input, option) =>
                        (option?.label?.toLowerCase()?.includes(input.toLowerCase()) ||
                            option?.value?.toLowerCase()?.includes(input.toLowerCase()))
                    }
                    options={selectOptions}
                    optionRender={(o) => <span>{o.label} ({o.value})</span>}
                />
                <Button
                    size='large'
                    shape='circle'
                    type="primary"
                    onClick={handleSelectSubmit}
                    icon={<ArrowUpOutlined />}
                    disabled={selectedSurveyID === undefined || store.experiment?.status !== 1 || agent === undefined}
                />
            </Row>
        </>
    );

    const renderMetrics = () => {
        const metrics = store.metrics;
        const clickedAgentID = store.clickedAgentID;

        let metricEntries = Array.from(metrics.entries())
            .map(
                ([key, values]) =>
                    [
                        key,
                        values.filter(
                            (v) =>
                                Number.isFinite(v.step) &&
                                Number.isFinite(v.value)
                        ),
                    ] as [string, ApiMetric[]]
            )
            .filter(([, values]) => values.length > 0);

        let sentimentMetrics: ApiMetric[] = [];
        let adoptedMetrics: ApiMetric[] = [];

        if (clickedAgentID !== undefined) {
            const agentSentimentKey = `sentiment:${clickedAgentID}`;
            const agentAdoptedKey = `adopted:${clickedAgentID}`;

            sentimentMetrics =
                metrics
                    .get(agentSentimentKey)
                    ?.filter(
                        (v) =>
                            Number.isFinite(v.step) &&
                            Number.isFinite(v.value)
                    ) ?? [];
            adoptedMetrics =
                metrics
                    .get(agentAdoptedKey)
                    ?.filter(
                        (v) =>
                            Number.isFinite(v.step) &&
                            Number.isFinite(v.value)
                    ) ?? [];

            metricEntries = metricEntries.filter(
                ([key]) =>
                    key !== agentSentimentKey &&
                    key !== agentAdoptedKey &&
                    !key.startsWith('sentiment') &&
                    !key.startsWith('adopted')
            );
        } else {
            metricEntries = metricEntries.filter(
                ([key]) =>
                    !key.startsWith('sentiment') &&
                    !key.startsWith('adopted')
            );
        }

        const hasSentimentAdoption =
            sentimentMetrics.length > 0 || adoptedMetrics.length > 0;

        if (!hasSentimentAdoption && metricEntries.length === 0) {
            return <div>{t('replay.chatbox.metrics.noMetrics')}</div>;
        }

        return (
            <div style={{ overflow: 'auto', height: '70vh', width: '100%' }}>
                {hasSentimentAdoption && (
                    <div key="sentiment-adoption">
                        <Plot
                            data={[
                                ...(sentimentMetrics.length > 0
                                    ? [
                                          {
                                              x: sentimentMetrics.map(
                                                  (v) => v.step
                                              ),
                                              y: sentimentMetrics.map(
                                                  (v) => v.value
                                              ),
                                              type: 'scatter' as PlotType,
                                              mode: 'lines+markers',
                                              name: 'sentiment',
                                              line: {
                                                  width: 2,
                                                  color: '#ff4d4f',
                                              },
                                              marker: {
                                                  size: 6,
                                                  color: '#ff4d4f',
                                              },
                                          } as PlotData,
                                      ]
                                    : []),
                                ...(adoptedMetrics.length > 0
                                    ? [
                                          {
                                              x: adoptedMetrics.map(
                                                  (v) => v.step
                                              ),
                                              y: adoptedMetrics.map(
                                                  (v) => v.value
                                              ),
                                              type: 'scatter' as PlotType,
                                              mode: 'lines+markers',
                                              name: 'adopted',
                                              line: {
                                                  width: 2,
                                                  color: '#52c41a',
                                              },
                                              marker: {
                                                  size: 6,
                                                  color: '#52c41a',
                                              },
                                          } as PlotData,
                                      ]
                                    : []),
                            ]}
                            layout={{
                                title: {
                                    text: t(
                                        'replay.chatbox.metrics.sentimentAdoption'
                                    ),
                                    font: {
                                        size: 16,
                                        family: 'Arial',
                                    },
                                },
                                autosize: true,
                                width: null,
                                height: 200,
                                margin: {
                                    l: 30,
                                    r: 10,
                                    t: 30,
                                    b: 30,
                                },
                                xaxis: {
                                    title: {
                                        text: t(
                                            'replay.chatbox.metrics.step'
                                        ),
                                        font: {
                                            size: 12,
                                        },
                                    },
                                    showgrid: true,
                                    gridcolor: '#f0f0f0',
                                },
                                yaxis: {
                                    title: {
                                        text: t(
                                            'replay.chatbox.metrics.value'
                                        ),
                                        font: {
                                            size: 12,
                                        },
                                    },
                                    showgrid: true,
                                    gridcolor: '#f0f0f0',
                                },
                                paper_bgcolor: 'rgba(0,0,0,0)',
                                plot_bgcolor: 'rgba(0,0,0,0)',
                            }}
                            config={{
                                responsive: true,
                                displaylogo: false,
                                modeBarButtonsToRemove: [
                                    'lasso2d',
                                    'select2d',
                                ],
                            }}
                            style={{ width: '100%', height: '100%' }}
                        />
                    </div>
                )}
                {metricEntries.map(([key, values]) => {
                    const x = values.map(v => v.step);
                    const y = values.map(v => v.value);

                    return (
                        <div key={key}>
                            <Plot
                                data={[
                                    {
                                        x: x,
                                        y: y,
                                        type: 'scatter' as PlotType,
                                        mode: 'lines+markers',
                                        name: key,
                                        line: {
                                            width: 2,
                                            color: '#1890ff'
                                        },
                                        marker: {
                                            size: 6,
                                            color: '#1890ff'
                                        }
                                    } as PlotData
                                ]}
                                layout={{
                                    title: {
                                        text: key,
                                        font: {
                                            size: 16,
                                            family: 'Arial'
                                        }
                                    },
                                    autosize: true,
                                    width: null,
                                    height: 200,
                                    margin: {
                                        l: 30,
                                        r: 10,
                                        t: 30,
                                        b: 30
                                    },
                                    xaxis: {
                                        title: {
                                            text: t('replay.chatbox.metrics.step'),
                                            font: {
                                                size: 12
                                            }
                                        },
                                        showgrid: true,
                                        gridcolor: '#f0f0f0'
                                    },
                                    yaxis: {
                                        title: {
                                            text: t('replay.chatbox.metrics.value'),
                                            font: {
                                                size: 12
                                            }
                                        },
                                        showgrid: true,
                                        gridcolor: '#f0f0f0'
                                    },
                                    paper_bgcolor: 'rgba(0,0,0,0)',
                                    plot_bgcolor: 'rgba(0,0,0,0)'
                                }}
                                config={{
                                    responsive: true,
                                    displaylogo: false,
                                    modeBarButtonsToRemove: ['lasso2d', 'select2d']
                                }}
                                style={{
                                    width: '100%',
                                    height: '100%'
                                }}
                            />
                        </div>
                    );
                })}
            </div>
        );
    };

    let model = new Model({});
    if (previewSurvey !== undefined) {
        try {
            model = new Model(JSON.parse(previewSurvey));
        } catch (e) {
            console.error('Failed to parse JSON data:', e);
        }
    }
    model.showCompleteButton = false;

    const rootClass = "right-inner";

    return (
        <>
            <Flex vertical className={rootClass}>
                <Tabs centered defaultActiveKey="0" animated={{ inkBar: true, tabPane: true }} className='tabs w-full'>
                    {agent !== undefined && <>
                        <Tabs.TabPane tab={t('replay.chatbox.tabs.reflection')} icon={<SmileOutlined />} key="0">
                            {renderDialogs(0)}
                        </Tabs.TabPane>
                        <Tabs.TabPane tab={t('replay.chatbox.tabs.agent')} icon={<AndroidOutlined />} key="1">
                            {renderDialogs(1)}
                        </Tabs.TabPane>
                        <Tabs.TabPane tab={t('replay.chatbox.tabs.user')} icon={<CommentOutlined />} key="2">
                            {renderDialogs(2)}
                        </Tabs.TabPane>
                        <Tabs.TabPane tab={t('replay.chatbox.tabs.survey')} icon={<ProfileOutlined />} key="3">
                            {renderSurveys()}
                        </Tabs.TabPane>
                    </>}
                    <Tabs.TabPane tab={t('replay.chatbox.tabs.metrics')} icon={<LineChartOutlined />} key="4">
                        {renderMetrics()}
                    </Tabs.TabPane>
                </Tabs>
            </Flex>
            <Modal
                width='50vw'
                title={t('replay.chatbox.survey.preview')}
                open={openPreview}
                onCancel={() => setOpenPreview(false)}
                footer={null}
            >
                <div style={{ overflow: 'auto', height: '70vh', width: '100%' }}>
                    <SurveyUI model={model} />
                </div>
            </Modal>
        </>
    );
});
