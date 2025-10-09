export default {
    day: "第{{day}}天",
    chatbox: {
        tabs: {
            reflection: "反思",
            agent: "智能体",
            user: "用户",
            survey: "问卷",
            relationshipGraph: "关系图",
            metrics: "指标"
        },
        survey: {
            preview: "预览",
            surveyName: "问卷名称",
            surveySent: "问卷已发送，请等待智能体将问卷保存到数据库并响应",
            messageSent: "消息已发送，请等待智能体将消息保存到数据库并响应",
            sendFailed: "发送失败："
        },
        dialog: {
            sendSuccess: "消息已发送，请等待智能体将消息保存到数据库并响应",
            adopted: "已采纳"
        },
        metrics: {
            noMetrics: "没有可用的指标数据",
            step: "步数",
            value: "值",
            sentimentAdoption: "情绪与采纳率",
            avgSentiment: "平均情绪",
            adoptionRate: "采纳率"
        },
        composer: {
            liveMode: "实时访谈，代理会立即给出回复。",
            postRunMode: "离线访谈，代理会在实验结束后异步生成回复。",
            readOnly: "当前实验不支持访谈。",
            timeline: "时间点：{{time}}",
            sentimentLabel: "情绪值 {{value}}",
            deltaLabel: "较 {{time}} 变化 {{delta}}",
            insertTemplate: "插入情绪变化提问模版",
            broadcastLabel: "群发目标",
            broadcastPlaceholder: "选择想要追问的其他代理",
            placeholder: "在此输入想向代理询问的问题…",
            hint: "按 Enter 发送，Shift+Enter 换行。",
            sendResult: "已向 {{success}} 位代理发送提问（{{failure}} 位失败）",
            sendFailure: "向 {{name}} 发送失败（状态 {{status}}）",
            needTarget: "请至少选择一名代理再发送。",
            template: "我注意到你在 {{changeTime}} 的情绪从 {{from}} 变为 {{to}}。能否分享一下到 {{currentTime}} 这段时间发生了什么？",
            noTimeline: "未选定时间轴"
        },
        relationshipGraph: {
            title: "关系图",
            refresh: "刷新关系图",
            error: "关系图加载失败：{{error}}",
            empty: "暂无关系图数据"
        }
    },
    relationshipLayout: {
        toggleGraph: "切换关系网络视图",
        resetLayout: "返回默认布局",
        loading: "正在加载关系图…",
        error: "关系图加载失败"
    },
    infoPanel: {
        title: "智能体信息",
        chooseAgent: "请在地图中选择一个智能体",
        unknown: "[未知]",
        currentStatus: "当前状态",
        statusHistory: "状态历史",
        name: "名称",
        id: "ID",
        showAsHeatmap: "点击显示为热力图",
        gender: "性别",
        age: "年龄",
        education: "教育水平",
        occupation: "职业",
        marriage_status: "婚姻状况",
        persona: "人格角色",
        background_story: "背景故事",
        status: "状态"
    },
    timelinePlayer: {
        replay: "回放",
        live: "直播",
        stepSpeed: {
            "10s": "10秒/步",
            "5s": "5秒/步",
            "2s": "2秒/步",
            "1s": "1秒/步",
            "0.5s": "0.5秒/步",
            "0.25s": "0.25秒/步",
            "0.1s": "0.1秒/步"
        }
    },
    summary: {
        title: "结果总结",
        adoptionRate: "总体采纳率",
        averageSentiment: "平均情绪",
        averageEmotion: "平均情绪分数",
        emotionDistribution: "情绪分布",
        analysisTitle: "AI分析结果",
        generateAnalysis: "生成AI分析结果",
        exportJson: "导出 JSON",
        exportCsv: "导出 CSV"
    }
};