import React, { useState, useEffect } from 'react';
import { Table, Button, Card, Space, Modal, message, Tooltip, Input, Popconfirm, Form, Col, Row, InputNumber, Select, Divider, Alert } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, CopyOutlined, ExportOutlined, MinusCircleOutlined, QuestionCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { ConfigWrapper, WorkflowStepConfig, ExpConfig } from '../../types/config';
import { WorkflowType } from '../../utils/enums';
import { fetchCustom } from '../../components/fetch';
import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';
import { Survey } from '../../components/type';
interface FormValues {
    name: string;
    description?: string;
    config: WorkflowStepConfig[];
}

const WorkflowList: React.FC = () => {
    const { t } = useTranslation();
    const [workflows, setWorkflows] = useState<ConfigWrapper<WorkflowStepConfig[]>[]>([]);
    const [loading, setLoading] = useState(false);
    const [searchText, setSearchText] = useState('');
    const [isModalVisible, setIsModalVisible] = useState(false);
    const [currentWorkflow, setCurrentWorkflow] = useState<ConfigWrapper<WorkflowStepConfig[]> | null>(null);
    const [functionList, setFunctionList] = useState<string[]>([]);
    const [surveyList, setSurveyList] = useState<Survey[]>([]);
    const [form] = Form.useForm<FormValues>();
    const [targetAgentModes, setTargetAgentModes] = useState<{ [key: string]: 'list' | 'expression' }>({});
    const [groupTargetAgentModes, setGroupTargetAgentModes] = useState<{ [key: string]: 'list' | 'expression' }>({});
    const [agentClasses, setAgentClasses] = useState<{ [agentType: string]: { value: string; label: string }[] }>({});
    const [loadingAgentClasses, setLoadingAgentClasses] = useState<{ [agentType: string]: boolean }>({});

    // 获取agent classes的函数
    const fetchAgentClasses = async () => {
        if (agentClasses['citizen'] && agentClasses['supervisor']) return; // 如果已经加载过，直接返回
        
        setLoadingAgentClasses({ citizen: true, supervisor: true });
        try {
            const [citizenResponse, supervisorResponse] = await Promise.all([
                fetchCustom('/api/agent-classes?agent_type=citizen'),
                fetchCustom('/api/agent-classes?agent_type=supervisor')
            ]);
            
            const citizenData = citizenResponse.ok ? await citizenResponse.json() : { data: [] };
            const supervisorData = supervisorResponse.ok ? await supervisorResponse.json() : { data: [] };
            
            setAgentClasses({
                citizen: citizenData.data || [],
                supervisor: supervisorData.data || []
            });
        } catch (error) {
            console.error('获取agent classes失败:', error);
            setAgentClasses({
                citizen: [],
                supervisor: []
            });
        } finally {
            setLoadingAgentClasses({ citizen: false, supervisor: false });
        }
    };

    // 初始化时获取所有agent classes
    useEffect(() => {
        fetchAgentClasses();
    }, []);

    // 获取函数列表
    useEffect(() => {
        const fetchFunctionList = async () => {
            try {
                const response = await fetchCustom('/api/community/workflow/functions');
                const data = await response.json();
                setFunctionList(data.data);
            } catch (error) {
                console.error('Failed to fetch function list:', error);
            }
        };
        fetchFunctionList();
    }, []);

    // 获取survey列表
    useEffect(() => {
        const fetchSurveyList = async () => {
            try {
                const response = await fetchCustom('/api/surveys');
                if (!response.ok) {
                    throw new Error(await response.text());
                }
                const data = await response.json();
                setSurveyList(data.data);
            } catch (error) {
                console.error('Failed to fetch survey list:', error);
            }
        };
        fetchSurveyList();
    }, []);

    // Load workflow configurations
    const loadWorkflows = async () => {
        setLoading(true);
        try {
            const res = await fetchCustom('/api/workflow-configs');
            if (!res.ok) {
                throw new Error(await res.text());
            }
            const data = (await res.json()).data as ConfigWrapper<WorkflowStepConfig[]>[];
            setWorkflows(data);
        } catch (error) {
            message.error(t('workflow.messages.loadFailed') + `: ${JSON.stringify(error.message)}`, 3);
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    // Initialize data
    useEffect(() => {
        const init = async () => {
            await loadWorkflows();
        };
        init();
    }, []);

    // Handle search
    const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
        setSearchText(e.target.value);
    };

    // Filter workflows based on search text
    const filteredWorkflows = workflows.filter(workflow =>
        workflow.name.toLowerCase().includes(searchText.toLowerCase()) ||
        (workflow.description && workflow.description.toLowerCase().includes(searchText.toLowerCase()))
    );

    // Handle create new workflow
    const handleCreate = () => {
        setCurrentWorkflow(null);
        form.setFieldsValue({
            name: `Workflow ${workflows.length + 1}`,
            description: '',
            config: [{
                type: WorkflowType.RUN,
                days: 1,
            }]
        });
        setIsModalVisible(true);
    };

    // Handle edit workflow
    const handleEdit = (workflow: ConfigWrapper<WorkflowStepConfig[]>) => {
        setCurrentWorkflow(workflow);

        // 处理配置数据，将AgentFilterConfig转换为表单格式
        const processedConfig = workflow.config?.map((step: any, index: number) => {
            if ([WorkflowType.INTERVIEW, WorkflowType.SURVEY, WorkflowType.UPDATE_STATE_INTERVENE, WorkflowType.MESSAGE_INTERVENE, WorkflowType.SAVE_CONTEXT, WorkflowType.MARKETING_MESSAGE].includes(step.type)) {
                if (step.type === WorkflowType.MARKETING_MESSAGE && step.groups) {
                    step.groups = step.groups.map((g: any, gIdx: number) => {
                        if (g.target_agent && typeof g.target_agent === 'object' && !Array.isArray(g.target_agent)) {
                            const agentFilter = g.target_agent as any;
                            if (agentFilter.filter_str) {
                                handleGroupTargetAgentModeChange(index, gIdx, 'expression');
                                return { ...g, target_agent: agentFilter.filter_str };
                            }
                        } else if (Array.isArray(g.target_agent)) {
                            handleGroupTargetAgentModeChange(index, gIdx, 'list');
                        } else if (typeof g.target_agent === 'string') {
                            handleGroupTargetAgentModeChange(index, gIdx, 'expression');
                        }
                        return g;
                    });
                    return step;
                }
                if (step.target_agent && typeof step.target_agent === 'object' && !Array.isArray(step.target_agent)) {
                    const agentFilter = step.target_agent as any;
                    if (agentFilter.filter_str) {
                        handleTargetAgentModeChange(index, 'expression');
                        const updated: any = { ...step, target_agent: agentFilter.filter_str };
                        if (agentFilter.agent_class && step.type !== WorkflowType.MARKETING_MESSAGE) {
                            fetchAgentClasses();
                            updated.agent_class = agentFilter.agent_class;
                        }
                        return updated;
                    }
                    if (agentFilter.agent_class && step.type !== WorkflowType.MARKETING_MESSAGE) {
                        handleTargetAgentModeChange(index, 'expression');
                        fetchAgentClasses();
                        return { ...step, agent_class: agentFilter.agent_class };
                    }
                } else if (Array.isArray(step.target_agent)) {
                    handleTargetAgentModeChange(index, 'list');
                } else if (typeof step.target_agent === 'string') {
                    handleTargetAgentModeChange(index, 'expression');
                }
            }
            return step;
        }) || [];

        form.setFieldsValue({
            name: workflow.name,
            description: workflow.description || '',
            config: processedConfig
        });
        setIsModalVisible(true);
    };

    // Handle duplicate workflow
    const handleDuplicate = (workflow: ConfigWrapper<WorkflowStepConfig[]>) => {
        setCurrentWorkflow(null);

        // 处理配置数据，将AgentFilterConfig转换为表单格式
        const processedConfig = workflow.config?.map((step: any, index: number) => {
            if ([WorkflowType.INTERVIEW, WorkflowType.SURVEY, WorkflowType.UPDATE_STATE_INTERVENE, WorkflowType.MESSAGE_INTERVENE, WorkflowType.SAVE_CONTEXT, WorkflowType.MARKETING_MESSAGE].includes(step.type)) {
                if (step.type === WorkflowType.MARKETING_MESSAGE && step.groups) {
                    step.groups = step.groups.map((g: any, gIdx: number) => {
                        if (g.target_agent && typeof g.target_agent === 'object' && !Array.isArray(g.target_agent)) {
                            const agentFilter = g.target_agent as any;
                            if (agentFilter.filter_str) {
                                handleGroupTargetAgentModeChange(index, gIdx, 'expression');
                                return { ...g, target_agent: agentFilter.filter_str };
                            }
                        } else if (Array.isArray(g.target_agent)) {
                            handleGroupTargetAgentModeChange(index, gIdx, 'list');
                        } else if (typeof g.target_agent === 'string') {
                            handleGroupTargetAgentModeChange(index, gIdx, 'expression');
                        }
                        return g;
                    });
                    return step;
                }
                if (step.target_agent && typeof step.target_agent === 'object' && !Array.isArray(step.target_agent)) {
                    const agentFilter = step.target_agent as any;
                    if (agentFilter.filter_str) {
                        handleTargetAgentModeChange(index, 'expression');
                        const updated: any = { ...step, target_agent: agentFilter.filter_str };
                        if (agentFilter.agent_class && step.type !== WorkflowType.MARKETING_MESSAGE) {
                            fetchAgentClasses();
                            updated.agent_class = agentFilter.agent_class;
                        }
                        return updated;
                    }
                    if (agentFilter.agent_class && step.type !== WorkflowType.MARKETING_MESSAGE) {
                        handleTargetAgentModeChange(index, 'expression');
                        fetchAgentClasses();
                        return { ...step, agent_class: agentFilter.agent_class };
                    }
                } else if (Array.isArray(step.target_agent)) {
                    handleTargetAgentModeChange(index, 'list');
                } else if (typeof step.target_agent === 'string') {
                    handleTargetAgentModeChange(index, 'expression');
                }
            }
            return step;
        }) || [];

        form.setFieldsValue({
            name: `${workflow.name} (Copy)`,
            description: workflow.description || '',
            config: processedConfig,
        });
        setIsModalVisible(true);
    };

    // Handle delete workflow
    const handleDelete = async (id: string) => {
        try {
            const res = await fetchCustom(`/api/workflow-configs/${id}`, {
                method: 'DELETE'
            });
            if (!res.ok) {
                throw new Error(await res.text());
            }
            message.success(t('workflow.messages.deleteSuccess'));
            loadWorkflows();
        } catch (error) {
            message.error(t('workflow.messages.deleteFailed') + `: ${JSON.stringify(error.message)}`, 3);
            console.error(error);
        }
    };

    // Handle export workflow
    const handleExport = (workflow: ConfigWrapper<WorkflowStepConfig[]>) => {
        const dataStr = JSON.stringify(workflow, null, 2);
        const dataUri = `data:application/json;charset=utf-8,${encodeURIComponent(dataStr)}`;

        const exportFileDefaultName = `${workflow.name.replace(/\s+/g, '_')}_workflow.json`;

        const linkElement = document.createElement('a');
        linkElement.setAttribute('href', dataUri);
        linkElement.setAttribute('download', exportFileDefaultName);
        linkElement.click();
    };

    // Handle modal OK
    const handleModalOk = async () => {
        try {
            // Validate form
            const formValues = await form.validateFields();

            // 处理 target_agent 字段
            if (formValues.config) {
                formValues.config = formValues.config.map((step: any, idx: number) => {
                    if ([
                        WorkflowType.INTERVIEW,
                        WorkflowType.SURVEY,
                        WorkflowType.UPDATE_STATE_INTERVENE,
                        WorkflowType.MESSAGE_INTERVENE,
                        WorkflowType.SAVE_CONTEXT,
                        WorkflowType.MARKETING_MESSAGE
                    ].includes(step.type)) {
                        if (targetAgentModes[idx] === 'expression' && typeof step.target_agent === 'string' && step.target_agent.trim()) {
                            step.target_agent = { filter_str: step.target_agent };
                        } else if (targetAgentModes[idx] === 'list') {
                            if (step.agent_class && step.agent_class.length > 0 && step.type !== WorkflowType.MARKETING_MESSAGE) {
                                step.target_agent = { agent_class: step.agent_class };
                                delete step.agent_class;
                            }
                        } else if (targetAgentModes[idx] === 'expression' && step.agent_class && step.agent_class.length > 0 && step.type !== WorkflowType.MARKETING_MESSAGE) {
                            step.target_agent = { filter_str: step.target_agent, agent_class: step.agent_class };
                            delete step.agent_class;
                        }
                        if (step.type === WorkflowType.MARKETING_MESSAGE && step.agent_class) {
                            delete step.agent_class;
                        }
                        if (step.type === WorkflowType.MARKETING_MESSAGE && step.groups) {
                            step.groups = step.groups.map((g: any, gIdx: number) => {
                                const mode = groupTargetAgentModes[`${idx}-${gIdx}`];
                                if (mode === 'expression' && typeof g.target_agent === 'string' && g.target_agent.trim()) {
                                    return { ...g, target_agent: { filter_str: g.target_agent } };
                                }
                                return g;
                            });
                            delete step.target_agent;
                            delete step.intervene_message;
                            delete step.reach_prob;
                            delete step.repeat;
                        }
                    }
                    return step;
                });
            }

            let res: Response;
            if (currentWorkflow) {
                res = await fetchCustom(`/api/workflow-configs/${currentWorkflow.id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(formValues),
                });
            } else {
                res = await fetchCustom('/api/workflow-configs', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(formValues),
                });
            }
            if (!res.ok) {
                throw new Error(await res.text());
            }
            message.success(currentWorkflow ? t('workflow.messages.updateSuccess') : t('workflow.messages.createSuccess'));
            setIsModalVisible(false);
            loadWorkflows();
        } catch (error) {
            message.error((currentWorkflow ? t('workflow.messages.updateFailed') : t('workflow.messages.createFailed')) + `: ${JSON.stringify(error.message)}`, 3);
            console.error('Validation failed:', error);
        }
    };

    // Handle modal cancel
    const handleModalCancel = () => {
        setIsModalVisible(false);
        form.resetFields();
    };

    // 处理 target_agent_mode 变化
    const handleTargetAgentModeChange = (stepIndex: number, mode: 'list' | 'expression') => {
        setTargetAgentModes(prev => ({
            ...prev,
            [stepIndex]: mode
        }));
    };

    const handleGroupTargetAgentModeChange = (stepIndex: number, groupIndex: number, mode: 'list' | 'expression') => {
        const key = `${stepIndex}-${groupIndex}`;
        setGroupTargetAgentModes(prev => ({
            ...prev,
            [key]: mode
        }));
    };

    // 在表单值变化时更新 target_agent_mode
    const handleFormValuesChange = (changedValues: any, allValues: FormValues) => {
        if (changedValues.config) {
            const config = allValues.config;
            config.forEach((step, index) => {
                if ([WorkflowType.INTERVIEW, WorkflowType.SURVEY, WorkflowType.UPDATE_STATE_INTERVENE, WorkflowType.MESSAGE_INTERVENE, WorkflowType.SAVE_CONTEXT, WorkflowType.MARKETING_MESSAGE].includes(step.type)) {
                    if (!targetAgentModes[index]) {
                        // 根据 target_agent 的值类型设置默认 mode
                        const targetAgent = step.target_agent;
                        if (Array.isArray(targetAgent)) {
                            handleTargetAgentModeChange(index, 'list');
                        } else if (typeof targetAgent === 'string') {
                            handleTargetAgentModeChange(index, 'expression');
                        } else if (targetAgent && typeof targetAgent === 'object') {
                            // 如果是AgentFilterConfig对象
                            const agentFilter = targetAgent as any;
                            if (agentFilter.filter_str) {
                                handleTargetAgentModeChange(index, 'expression');
                            } else if (agentFilter.agent_class) {
                                handleTargetAgentModeChange(index, 'list');
                            }
                        } else {
                            handleTargetAgentModeChange(index, 'list');
                        }
                    }
                }
            });
        }
    };

    // Table columns
    const columns = [
        {
            title: t('common.name'),
            dataIndex: 'name',
            key: 'name',
            sorter: (a: ConfigWrapper<WorkflowStepConfig[]>, b: ConfigWrapper<WorkflowStepConfig[]>) => a.name.localeCompare(b.name)
        },
        {
            title: t('common.description'),
            dataIndex: 'description',
            key: 'description',
            ellipsis: true
        },
        {
            title: t('common.lastUpdated'),
            dataIndex: 'updated_at',
            key: 'updated_at',
            render: (text: string) => dayjs(text).format('YYYY-MM-DD HH:mm:ss'),
            sorter: (a: ConfigWrapper<WorkflowStepConfig[]>, b: ConfigWrapper<WorkflowStepConfig[]>) => dayjs(a.updated_at).valueOf() - dayjs(b.updated_at).valueOf()
        },
        {
            title: t('common.actions'),
            key: 'actions',
            render: (_: any, record: ConfigWrapper<WorkflowStepConfig[]>) => (
                <Space size="small">
                    {
                        (record.tenant_id ?? '') !== '' && (
                            <Tooltip title={t('common.edit')}>
                                <Button icon={<EditOutlined />} size="small" onClick={() => handleEdit(record)} />
                            </Tooltip>
                        )
                    }
                    <Tooltip title={t('common.duplicate')}>
                        <Button icon={<CopyOutlined />} size="small" onClick={() => handleDuplicate(record)} />
                    </Tooltip>
                    <Tooltip title={t('common.export')}>
                        <Button icon={<ExportOutlined />} size="small" onClick={() => handleExport(record)} />
                    </Tooltip>
                    {
                        (record.tenant_id ?? '') !== '' && (
                            <Tooltip title={t('common.delete')}>
                                <Popconfirm
                                    title={t('workflow.deleteConfirm')}
                                    onConfirm={() => handleDelete(record.id)}
                                    okText={t('common.submit')}
                                    cancelText={t('common.cancel')}
                                >
                                    <Button icon={<DeleteOutlined />} size="small" danger />
                                </Popconfirm>
                            </Tooltip>
                        )
                    }
                </Space>
            )
        }
    ];

    return (
        <Card
            title={t('workflow.title')}
            extra={<Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>{t('workflow.createNew')}</Button>}
        >
            <Input.Search
                placeholder={t('workflow.searchPlaceholder')}
                onChange={handleSearch}
                style={{ marginBottom: 8 }}
            />

            <Table
                columns={columns}
                dataSource={filteredWorkflows}
                rowKey="id"
                loading={loading}
                pagination={{ pageSize: 10, size: 'small', showSizeChanger: false }}
            />

            <Modal
                title={currentWorkflow ? t('workflow.editTitle') : t('workflow.createTitle')}
                open={isModalVisible}
                onOk={handleModalOk}
                onCancel={handleModalCancel}
                width="75vw"
                destroyOnHidden
            >
                <Form
                    form={form}
                    layout="vertical"
                    onValuesChange={handleFormValuesChange}
                >
                    {/* 元数据部分 */}
                    <Card
                        title={t('common.metadataTitle')}
                        style={{ marginBottom: 8 }}
                        bodyStyle={{ padding: '12px' }}
                        headStyle={{ padding: '8px 12px' }}
                    >
                        <Row gutter={8}>
                            <Col span={8}>
                                <Form.Item
                                    name="name"
                                    label={t('common.name')}
                                    rules={[{ required: true, message: t('common.nameRequired') }]}
                                    style={{ marginBottom: 8 }}
                                >
                                    <Input placeholder={t('common.namePlaceholder')} />
                                </Form.Item>
                            </Col>
                            <Col span={16}>
                                <Form.Item
                                    name="description"
                                    label={t('common.description')}
                                    style={{ marginBottom: 8 }}
                                >
                                    <Input.TextArea
                                        rows={1}
                                        placeholder={t('common.descriptionPlaceholder')}
                                    />
                                </Form.Item>
                            </Col>
                        </Row>
                    </Card>

                    {/* 工作流配置部分 */}
                    <Card
                        title={t('workflow.settingsTitle')}
                    >
                        {/* 文档链接提示 */}
                        <Alert
                            message={
                                <Space size={4}>
                                    <InfoCircleOutlined />
                                    {t('workflow.documentationTip')}
                                    <a 
                                        href="https://agentsociety.readthedocs.io/en/latest/03-config/05-exp.html#workflowstep" 
                                        target="_blank" 
                                        rel="noopener noreferrer"
                                    >
                                        {t('workflow.documentationLink')}
                                    </a>
                                </Space>
                            }
                            type="info"
                            showIcon={false}
                            style={{ marginBottom: 16 }}
                        />
                        <Form.List
                            name="config"
                        >
                            {(fields, { add, remove }) => (
                                <>
                                    {fields.map(({ key, name, ...restField }, index) => (
                                        <>
                                            {/* 基本配置行 */}
                                            <Row gutter={8} align="middle" style={{ marginBottom: 8 }}>
                                                <Col span={6}>
                                                    <Form.Item
                                                        {...restField}
                                                        name={[name, 'type']}
                                                        label={t('workflow.stepType', { number: name + 1 })}
                                                        rules={[{ required: true, message: t('workflow.pleaseSelectStepType') }]}
                                                        style={{ marginBottom: 8 }}
                                                    >
                                                        <Select
                                                            style={{ width: '100%' }}
                                                            placeholder={t('workflow.selectStepType')}
                                                            options={[
                                                                {
                                                                    value: WorkflowType.RUN,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.run')}
                                                                            <Tooltip title={t('workflow.runTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.STEP,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.step')}
                                                                            <Tooltip title={t('workflow.stepTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.ENVIRONMENT_INTERVENE,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.environmentIntervene')}
                                                                            <Tooltip title={t('workflow.environmentInterveneTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.UPDATE_STATE_INTERVENE,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.update_state_intervene')}
                                                                            <Tooltip title={t('workflow.update_state_interveneTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.MESSAGE_INTERVENE,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.message_intervene')}
                                                                            <Tooltip title={t('workflow.message_interveneTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.MARKETING_MESSAGE,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.marketing_message')}
                                                                            <Tooltip title={t('workflow.marketing_messageTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.SURVEY,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.survey')}
                                                                            <Tooltip title={t('workflow.surveyTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.NEXT_ROUND,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.nextRound')}
                                                                            <Tooltip title={t('workflow.nextRoundTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.INTERVIEW,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.interview')}
                                                                            <Tooltip title={t('workflow.interviewTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.FUNCTION,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.function')}
                                                                            <Tooltip title={t('workflow.functionTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                                {
                                                                    value: WorkflowType.SAVE_CONTEXT,
                                                                    label: (
                                                                        <Space size={4}>
                                                                            {t('workflow.saveContext')}
                                                                            <Tooltip title={t('workflow.saveContextTooltip')}>
                                                                                <QuestionCircleOutlined style={{ color: '#1890ff' }} />
                                                                            </Tooltip>
                                                                        </Space>
                                                                    )
                                                                },
                                                            ]}
                                                        />
                                                    </Form.Item>
                                                </Col>
                                                <Col span={7}>
                                                    <Form.Item
                                                        {...restField}
                                                        name={[name, 'description']}
                                                        label={t('workflow.description')}
                                                        tooltip={t('workflow.descriptionTooltip')}
                                                        style={{ marginBottom: 8 }}
                                                    >
                                                        <Input placeholder={t('workflow.enterStepDescription')} />
                                                    </Form.Item>
                                                </Col>
                                                {/* 动态字段渲染 */}
                                                <Form.Item shouldUpdate noStyle>
                                                    {() => {
                                                        const workflowSteps = form.getFieldValue('config') || [];
                                                        const currentStep = workflowSteps[name];
                                                        const stepType = currentStep?.type;

                                                        if (stepType === WorkflowType.RUN) {
                                                            return (
                                                                <>
                                                                    <Col span={6}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'days']}
                                                                            label={t('workflow.days')}
                                                                            rules={[{ required: true, message: t('workflow.pleaseEnterDays') }]}
                                                                            tooltip={t('workflow.daysTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <InputNumber min={0} style={{ width: '100%' }} />
                                                                        </Form.Item>
                                                                    </Col>
                                                                    <Col span={6}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'ticks_per_step']}
                                                                            label={t('workflow.ticksPerStep')}
                                                                            initialValue={300}
                                                                            tooltip={t('workflow.ticksPerStepTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <InputNumber min={1} style={{ width: '100%' }} />
                                                                        </Form.Item>
                                                                    </Col>
                                                                </>
                                                            );
                                                        }

                                                        if (stepType === WorkflowType.STEP) {
                                                            return (
                                                                <>
                                                                    <Col span={6}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'steps']}
                                                                            label={t('workflow.steps')}
                                                                            initialValue={1}
                                                                            rules={[{ required: true, message: t('workflow.pleaseEnterSteps') }]}
                                                                            tooltip={t('workflow.stepsTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <InputNumber min={1} style={{ width: '100%' }} />
                                                                        </Form.Item>
                                                                    </Col>
                                                                    <Col span={6}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'ticks_per_step']}
                                                                            label={t('workflow.ticksPerStep')}
                                                                            initialValue={300}
                                                                            tooltip={t('workflow.ticksPerStepTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <InputNumber min={1} style={{ width: '100%' }} />
                                                                        </Form.Item>
                                                                    </Col>
                                                                </>
                                                            );
                                                        }

                                                        if (stepType === WorkflowType.ENVIRONMENT_INTERVENE) {
                                                            return (
                                                                <>
                                                                    <Col span={6}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'key']}
                                                                            label={t('workflow.environmentKey')}
                                                                            rules={[{ required: true, message: t('workflow.pleaseEnterEnvironmentKey') }]}
                                                                            tooltip={t('workflow.environmentKeyTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <Input placeholder={t('workflow.enterEnvironmentKey')} />
                                                                        </Form.Item>
                                                                    </Col>
                                                                    <Col span={6}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'value']}
                                                                            label={t('workflow.environmentValue')}
                                                                            rules={[{ required: true, message: t('workflow.pleaseEnterEnvironmentValue') }]}
                                                                            tooltip={t('workflow.environmentValueTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <Input.TextArea rows={1} placeholder={t('workflow.enterEnvironmentValue')} />
                                                                        </Form.Item>
                                                                    </Col>
                                                                </>
                                                            );
                                                        }

                                                        if ([WorkflowType.INTERVIEW, WorkflowType.SURVEY, WorkflowType.UPDATE_STATE_INTERVENE, WorkflowType.MESSAGE_INTERVENE, WorkflowType.SAVE_CONTEXT, WorkflowType.MARKETING_MESSAGE].includes(stepType)) {
                                                            return (
                                                                <>
                                                                    <Col span={12}>
                                                                        <Form.Item
                                                                            label={t('workflow.targetAgentMode')}
                                                                            tooltip={t('workflow.targetAgentModeTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <Select
                                                                                value={targetAgentModes[name] || 'list'}
                                                                                onChange={(value) => handleTargetAgentModeChange(name, value)}
                                                                                options={[
                                                                                    { value: 'list', label: t('workflow.targetAgentModeList') },
                                                                                    { value: 'expression', label: t('workflow.targetAgentModeExpression') }
                                                                                ]}
                                                                            />
                                                                        </Form.Item>
                                                                    </Col>
                                                                    {targetAgentModes[name] === 'list' ? (
                                                                        <Col span={12}>
                                                                            <Form.Item
                                                                                {...restField}
                                                                                name={[name, 'target_agent']}
                                                                                label={t('workflow.targetAgentIds')}
                                                                                rules={[{ required: true, message: t('workflow.pleaseEnterTargetAgent') }]}
                                                                                tooltip={t('workflow.targetAgentIdsTooltip')}
                                                                                style={{ marginBottom: 8 }}
                                                                            >
                                                                                <Input
                                                                                    placeholder="1,2,3"
                                                                                    onChange={(e) => {
                                                                                        const value = e.target.value.split(',').map(v => parseInt(v.trim()));
                                                                                        form.setFieldValue(['config', name, 'target_agent'], value);
                                                                                    }}
                                                                                />
                                                                            </Form.Item>
                                                                        </Col>
                                                                    ) : (
                                                                        <>
                                                                            <Col span={12}>
                                                                                <Form.Item
                                                                                    {...restField}
                                                                                    name={[name, 'target_agent']}
                                                                                    label={t('workflow.targetAgentExpression')}
                                                                                    tooltip={t('workflow.targetAgentExpressionTooltip')}
                                                                                    style={{ marginBottom: 8 }}
                                                                                >
                                                                                    <Input placeholder={t('workflow.targetAgentExpressionPlaceholder')} />
                                                                                </Form.Item>
                                                                            </Col>
                                                                            {stepType !== WorkflowType.MARKETING_MESSAGE && (
                                                                                <Col span={12}>
                                                                                    <Form.Item
                                                                                        {...restField}
                                                                                        name={[name, 'agent_class']}
                                                                                        label={t('workflow.agentClass')}
                                                                                        tooltip={t('workflow.agentClassTooltip')}
                                                                                        style={{ marginBottom: 8 }}
                                                                                    >
                                                                                        <Select
                                                                                            mode="multiple"
                                                                                            placeholder={t('workflow.selectAgentClass')}
                                                                                            loading={loadingAgentClasses['citizen'] || loadingAgentClasses['supervisor']}
                                                                                            options={[
                                                                                                {
                                                                                                    label: t('workflow.agentClassGroups.citizen'),
                                                                                                    options: (agentClasses['citizen'] || []).map(item => ({
                                                                                                        ...item,
                                                                                                        label: item.label
                                                                                                    }))
                                                                                                },
                                                                                                {
                                                                                                    label: t('workflow.agentClassGroups.supervisor'),
                                                                                                    options: (agentClasses['supervisor'] || []).map(item => ({
                                                                                                        ...item,
                                                                                                        label: item.label
                                                                                                    }))
                                                                                                }
                                                                                            ]}
                                                                                        />
                                                                                    </Form.Item>
                                                                                </Col>
                                                                            )}
                                                                        </>
                                                                    )}
                                                                    {stepType === WorkflowType.INTERVIEW && (
                                                                        <Col span={12}>
                                                                            <Form.Item
                                                                                {...restField}
                                                                                name={[name, 'interview_message']}
                                                                                label={t('workflow.interviewMessage')}
                                                                                rules={[{ required: true, message: t('workflow.pleaseEnterInterviewMessage') }]}
                                                                                tooltip={t('workflow.interviewMessageTooltip')}
                                                                                style={{ marginBottom: 8 }}
                                                                            >
                                                                                <Input.TextArea rows={1} style={{ height: '32px' }} />
                                                                            </Form.Item>
                                                                        </Col>
                                                                    )}
                                                                    {stepType === WorkflowType.SURVEY && (
                                                                        <Col span={12}>
                                                                            <Form.Item
                                                                                {...restField}
                                                                                name={[name, 'survey']}
                                                                                label={t('workflow.survey')}
                                                                                rules={[{ required: true, message: t('workflow.pleaseEnterSurvey') }]}
                                                                                tooltip={t('workflow.surveyTooltip')}
                                                                                style={{ marginBottom: 8 }}
                                                                            >
                                                                                <Select
                                                                                    placeholder={t('workflow.selectSurvey')}
                                                                                    options={surveyList.map(survey => ({
                                                                                        value: survey.id,
                                                                                        label: survey.name
                                                                                    }))}
                                                                                />
                                                                            </Form.Item>
                                                                        </Col>
                                                                    )}
                                                                    {stepType === WorkflowType.UPDATE_STATE_INTERVENE && (
                                                                        <>
                                                                            <Col span={6}>
                                                                                <Form.Item
                                                                                    {...restField}
                                                                                    name={[name, 'key']}
                                                                                    label={t('workflow.update_state_intervene')}
                                                                                    rules={[{ required: true, message: t('workflow.pleaseEnterEnvironmentKey') }]}
                                                                                    tooltip={t('workflow.environmentKeyTooltip')}
                                                                                    style={{ marginBottom: 8 }}
                                                                                >
                                                                                    <Input placeholder={t('workflow.enterEnvironmentKey')} />
                                                                                </Form.Item>
                                                                            </Col>
                                                                            <Col span={6}>
                                                                                <Form.Item
                                                                                    {...restField}
                                                                                    name={[name, 'value']}
                                                                                    label={t('workflow.environmentValue')}
                                                                                    rules={[{ required: true, message: t('workflow.pleaseEnterEnvironmentValue') }]}
                                                                                    tooltip={t('workflow.environmentValueTooltip')}
                                                                                    style={{ marginBottom: 8 }}
                                                                                >
                                                                                    <Input.TextArea rows={1} placeholder={t('workflow.enterEnvironmentValue')} />
                                                                                </Form.Item>
                                                                            </Col>
                                                                        </>
                                                                    )}
                                                                    {stepType === WorkflowType.MESSAGE_INTERVENE && (
                                                                        <Col span={12}>
                                                                            <Form.Item
                                                                                {...restField}
                                                                                name={[name, 'intervene_message']}
                                                                                label={t('workflow.message_intervene')}
                                                                                rules={[{ required: true, message: t('workflow.pleaseEnterInterveneMessage') }]}
                                                                                tooltip={t('workflow.interveneMessageTooltip')}
                                                                                style={{ marginBottom: 8 }}
                                                                            >
                                                                                <Input.TextArea rows={1} style={{ height: '32px' }} />
                                                                            </Form.Item>
                                                                        </Col>
                                                                    )}
                                                                        {stepType === WorkflowType.MARKETING_MESSAGE && (
                                                                            <Form.List name={[name, 'groups']}>
                                                                                {(fields, { add, remove }) => {
                                                                                    if (fields.length === 0) {
                                                                                        add();
                                                                                        return null;
                                                                                    }
                                                                                    return (
                                                                                    <>
                                                                                        {fields.map((field, gIdx) => (
                                                                                            <Row key={field.key} gutter={8} align="middle">
                                                                                                <Col span={12}>
                                                                                                    <Form.Item
                                                                                                        {...field}
                                                                                                        name={[field.name, 'description']}
                                                                                                        label={t('workflow.description')}
                                                                                                        tooltip={t('workflow.descriptionTooltip')}
                                                                                                        style={{ marginBottom: 8 }}
                                                                                                    >
                                                                                                        <Input placeholder={t('workflow.enterStepDescription')} />
                                                                                                    </Form.Item>
                                                                                                </Col>
                                                                                                <Col span={6}>
                                                                                                    <Form.Item
                                                                                                        {...field}
                                                                                                        name={[field.name, 'send_time']}
                                                                                                        label={t('workflow.sendTime')}
                                                                                                        tooltip={t('workflow.sendTimeTooltip')}
                                                                                                        style={{ marginBottom: 8 }}
                                                                                                    >
                                                                                                        <Input placeholder={t('workflow.enterSendTime')} />
                                                                                                    </Form.Item>
                                                                                                </Col>
                                                                                                <Col span={6}>
                                                                                                    <Form.Item
                                                                                                        label={t('workflow.targetAgentMode')}
                                                                                                        tooltip={t('workflow.targetAgentModeTooltip')}
                                                                                                        style={{ marginBottom: 8 }}
                                                                                                    >
                                                                                                        <Select
                                                                                                            value={groupTargetAgentModes[`${name}-${gIdx}`] || 'list'}
                                                                                                            onChange={(value) => handleGroupTargetAgentModeChange(name, gIdx, value)}
                                                                                                            options={[
                                                                                                                { value: 'list', label: t('workflow.targetAgentModeList') },
                                                                                                                { value: 'expression', label: t('workflow.targetAgentModeExpression') }
                                                                                                            ]}
                                                                                                        />
                                                                                                    </Form.Item>
                                                                                                </Col>
                                                                                                {groupTargetAgentModes[`${name}-${gIdx}`] === 'list' ? (
                                                                                                    <Col span={12}>
                                                                                                        <Form.Item
                                                                                                            {...field}
                                                                                                            name={[field.name, 'target_agent']}
                                                                                                            label={t('workflow.targetAgentIds')}
                                                                                                            rules={[{ required: true, message: t('workflow.pleaseEnterTargetAgent') }]}
                                                                                                            tooltip={t('workflow.targetAgentIdsTooltip')}
                                                                                                            style={{ marginBottom: 8 }}
                                                                                                        >
                                                                                                            <Input
                                                                                                                placeholder="1,2,3"
                                                                                                                onChange={(e) => {
                                                                                                                    const value = e.target.value.split(',').map(v => parseInt(v.trim())).filter(v => !isNaN(v));
                                                                                                                    form.setFieldValue(['config', name, 'groups', gIdx, 'target_agent'], value);
                                                                                                                }}
                                                                                                            />
                                                                                                        </Form.Item>
                                                                                                    </Col>
                                                                                                ) : (
                                                                                                    <Col span={12}>
                                                                                                        <Form.Item
                                                                                                            {...field}
                                                                                                            name={[field.name, 'target_agent']}
                                                                                                            label={t('workflow.targetAgentExpression')}
                                                                                                            tooltip={t('workflow.targetAgentExpressionTooltip')}
                                                                                                            style={{ marginBottom: 8 }}
                                                                                                        >
                                                                                                            <Input placeholder={t('workflow.targetAgentExpressionPlaceholder')} />
                                                                                                        </Form.Item>
                                                                                                    </Col>
                                                                                                )}
                                                                                                <Col span={12}>
                                                                                                    <Form.Item
                                                                                                        {...field}
                                                                                                        name={[field.name, 'intervene_message']}
                                                                                                        label={t('workflow.marketing_message')}
                                                                                                        rules={[{ required: true, message: t('workflow.pleaseEnterMarketingMessage') }]}
                                                                                                        tooltip={t('workflow.marketing_messageTooltip')}
                                                                                                        style={{ marginBottom: 8 }}
                                                                                                    >
                                                                                                        <Input.TextArea rows={1} style={{ height: '32px' }} />
                                                                                                    </Form.Item>
                                                                                                </Col>
                                                                                                <Col span={6}>
                                                                                                    <Form.Item
                                                                                                        {...field}
                                                                                                        name={[field.name, 'reach_prob']}
                                                                                                        label={t('workflow.reachProbability')}
                                                                                                        rules={[{ required: true, message: t('workflow.pleaseEnterReachProb') }]}
                                                                                                        tooltip={t('workflow.reachProbability')}
                                                                                                        style={{ marginBottom: 8 }}
                                                                                                    >
                                                                                                        <Input type="number" min={0} max={1} step={0.01} />
                                                                                                    </Form.Item>
                                                                                                </Col>
                                                                                                <Col span={6}>
                                                                                                    <Form.Item
                                                                                                        {...field}
                                                                                                        name={[field.name, 'repeat']}
                                                                                                        label={t('workflow.repeat')}
                                                                                                        tooltip={t('workflow.repeat')}
                                                                                                        initialValue={1}
                                                                                                        style={{ marginBottom: 8 }}
                                                                                                    >
                                                                                                        <InputNumber min={1} />
                                                                                                    </Form.Item>
                                                                                                </Col>
                                                                                                <Col span={2}>
                                                                                                    <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                                                                                                </Col>
                                                                                            </Row>
                                                                                        ))}
                                                                                        <Form.Item style={{ marginBottom: 8 }}>
                                                                                            <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />}>
                                                                                                {t('workflow.addReachGroup')}
                                                                                            </Button>
                                                                                        </Form.Item>
                                                                                    </>
                                                                                    );
                                                                                }}
                                                                            </Form.List>
                                                                        )}
                                                                    {stepType === WorkflowType.SAVE_CONTEXT && (
                                                                        <>
                                                                            <Col span={6}>
                                                                                <Form.Item
                                                                                    {...restField}
                                                                                    name={[name, 'key']}
                                                                                    label={t('workflow.contextKey')}
                                                                                    rules={[{ required: true, message: t('workflow.pleaseEnterContextKey') }]}
                                                                                    tooltip={t('workflow.contextKeyTooltip')}
                                                                                    style={{ marginBottom: 8 }}
                                                                                >
                                                                                    <Input placeholder={t('workflow.enterContextKey')} />
                                                                                </Form.Item>
                                                                            </Col>
                                                                            <Col span={6}>
                                                                                <Form.Item
                                                                                    {...restField}
                                                                                    name={[name, 'save_as']}
                                                                                    label={t('workflow.saveAs')}
                                                                                    rules={[{ required: true, message: t('workflow.pleaseEnterSaveAs') }]}
                                                                                    tooltip={t('workflow.saveAsTooltip')}
                                                                                    style={{ marginBottom: 8 }}
                                                                                >
                                                                                    <Input placeholder={t('workflow.enterSaveAs')} />
                                                                                </Form.Item>
                                                                            </Col>
                                                                        </>
                                                                    )}
                                                                </>
                                                            );
                                                        }

                                                        if (stepType === WorkflowType.FUNCTION) {
                                                            return (
                                                                <>
                                                                    <Col span={12}>
                                                                        <Form.Item
                                                                            {...restField}
                                                                            name={[name, 'func']}
                                                                            label={t('workflow.functionName')}
                                                                            rules={[{ required: true, message: t('workflow.pleaseSelectFunction') }]}
                                                                            tooltip={t('workflow.functionNameTooltip')}
                                                                            style={{ marginBottom: 8 }}
                                                                        >
                                                                            <Select
                                                                                placeholder={t('workflow.selectFunction')}
                                                                                options={functionList.map(func => ({
                                                                                    value: func,
                                                                                    label: func
                                                                                }))}
                                                                            />
                                                                        </Form.Item>
                                                                    </Col>
                                                                </>
                                                            );
                                                        }

                                                        return null;
                                                    }}
                                                </Form.Item>
                                                <Col span={1}>
                                                    <Button
                                                        type="text"
                                                        danger
                                                        icon={<MinusCircleOutlined />}
                                                        onClick={() => remove(name)}
                                                        size="small"
                                                    />
                                                </Col>
                                            </Row>
                                            <Divider />
                                        </>
                                    ))}
                                    <Form.Item style={{ marginBottom: 0 }}>
                                        <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />} size="small">
                                            {t('workflow.addWorkflowStep')}
                                        </Button>
                                    </Form.Item>
                                </>
                            )}
                        </Form.List>
                    </Card>
                </Form>
            </Modal>
        </Card>
    );
};

export default WorkflowList; 
