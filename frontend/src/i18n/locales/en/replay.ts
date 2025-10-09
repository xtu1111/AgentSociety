export default {
    day: "Day {{day}}",
    chatbox: {
        tabs: {
            reflection: "Reflection",
            agent: "Agent",
            user: "User",
            survey: "Survey",
            relationshipGraph: "Relationship Graph",
            metrics: "Metrics"
        },
        survey: {
            preview: "Preview",
            surveyName: "Survey Name",
            surveySent: "Survey sent, you should wait for the agent to save the survey into database and respond",
            messageSent: "Message sent, you should wait for the agent to save the message into database and respond",
            sendFailed: "Failed to send:"
        },
        dialog: {
            sendSuccess: "Message sent, you should wait for the agent to save the message into database and respond",
            adopted: "Adopted"
        },
        metrics: {
            noMetrics: "No metrics data available",
            step: "Step",
            value: "Value",
            sentimentAdoption: "Sentiment & Adoption",
            avgSentiment: "Average Sentiment",
            adoptionRate: "Adoption Rate"
        },
        composer: {
            liveMode: "Live interview. Responses will arrive in real time.",
            postRunMode: "Post-run interview. Responses are generated asynchronously.",
            readOnly: "Interviewing is disabled for this experiment.",
            timeline: "Timeline: {{time}}",
            sentimentLabel: "Sentiment {{value}}",
            deltaLabel: "Δ{{delta}} since {{time}}",
            insertTemplate: "Insert change-tracking template",
            broadcastLabel: "Broadcast targets",
            broadcastPlaceholder: "Select additional agents to interview",
            placeholder: "Type your question for the selected agents…",
            hint: "Press Enter to send, Shift+Enter for a new line.",
            sendResult: "Sent question to {{success}} agent(s) ({{failure}} failed)",
            sendFailure: "Failed to send to {{name}} (status {{status}})",
            needTarget: "Select at least one agent to interview.",
            template: "I noticed around {{changeTime}} your sentiment shifted from {{from}} to {{to}}. Could you share what happened by {{currentTime}}?",
            noTimeline: "No timeline selected"
        },
        relationshipGraph: {
            title: "Relationship graph",
            refresh: "Reload graph",
            error: "Failed to load relationship graph: {{error}}",
            empty: "Relationship graph is not available."
        }
    },
    relationshipLayout: {
        toggleGraph: "Toggle Relationship Graph",
        resetLayout: "Back to Default Layout",
        loading: "Loading relationship graph…",
        error: "Unable to load relationship graph."
    },
    infoPanel: {
        title: "Agent Information",
        chooseAgent: "Please choose an agent in map",
        unknown: "[Unknown]",
        currentStatus: "Current Status",
        statusHistory: "Status History",
        name: "name",
        id: "ID",
        showAsHeatmap: "Click to show as heatmap",
        gender: "Gender",
        age: "Age",
        education: "Education",
        occupation: "Occupation",
        marriage_status: "Marriage Status",
        persona: "Persona",
        background_story: "Background Story",
        status: "Status"
    },
    timelinePlayer: {
        replay: "Replay",
        live: "Live",
        stepSpeed: {
            "10s": "10s/step",
            "5s": "5s/step",
            "2s": "2s/step",
            "1s": "1s/step",
            "0.5s": "0.5s/step",
            "0.25s": "0.25s/step",
            "0.1s": "0.1s/step"
        }
    },
    summary: {
        title: "Result Summary",
        adoptionRate: "Adoption Rate",
        averageSentiment: "Average Sentiment",
        averageEmotion: "Average Emotion",
        emotionDistribution: "Emotion Distribution",
        analysisTitle: "AI Analysis",
        generateAnalysis: "Generate AI Analysis",
        exportJson: "Export JSON",
        exportCsv: "Export CSV"
    }
};