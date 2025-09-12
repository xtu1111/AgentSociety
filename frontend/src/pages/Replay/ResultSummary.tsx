import React, { useContext, useState } from "react";
import { Button, Drawer, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { StoreContext } from "./store";
import { ExperimentSummary } from "./components/type";
import { fetchCustom } from "../../components/fetch";

const ResultSummary: React.FC = () => {
    const { t } = useTranslation();
    const emotionLabels: Record<string, string> = {
        interested: t('emotion.interested'),
        curious: t('emotion.curious'),
        relaxed: t('emotion.relaxed'),
        neutral: t('emotion.neutral'),
        uninterested: t('emotion.uninterested'),
        skeptical: t('emotion.skeptical'),
        dislike: t('emotion.dislike')
    };
    const EMOTION_ORDER = [
    "interested",
    "curious",
    "relaxed",
    "neutral",
    "uninterested",
    "skeptical",
    "dislike"
    ];
    const store = useContext(StoreContext);
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [summary, setSummary] = useState<ExperimentSummary>();

    const loadSummary = async () => {
        if (!store.expID) {
            return;
        }
        setLoading(true);
        try {
            const res = await fetchCustom(`/api/experiments/${store.expID}/summary`);
            const data = await res.json();
            setSummary(data.data as ExperimentSummary);
        } catch (err) {
            console.error("failed to fetch summary", err);
        } finally {
            setLoading(false);
        }
    };

    const handleOpen = () => {
        setOpen(true);
        if (!summary) {
            loadSummary();
        }
    };

    const exportJSON = () => {
        if (!summary || !store.expID) return;
        const blob = new Blob([JSON.stringify(summary, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `summary_${store.expID}.json`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const exportCSV = () => {
        if (!summary || !store.expID) return;
        const rows: string[][] = [["metric", "value"]];
        rows.push(["adoption_rate", summary.adoption_rate.toString()]);
        if (summary.average_sentiment != null) {
            rows.push(["average_sentiment", summary.average_sentiment.toString()]);
        }
        if (summary.overall_average_emotion != null) {
            rows.push(["average_emotion_overall", summary.overall_average_emotion.toString()]);
        }
        if (summary.average_emotion) {
            Object.entries(summary.average_emotion).forEach(([k, v]) => {
                rows.push([`avg_${k}`, (v * 100).toFixed(1) + "%"]);
            });
        }
        Object.entries(summary.emotion_distribution).forEach(([k, v]) => {
            rows.push([`emotion_${k}`, v.toString()]);
        });
        const csv = rows.map(r => r.join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `summary_${store.expID}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <>
            <Button style={{ position: "absolute", top: 20, right: 20, zIndex: 1000 }} onClick={handleOpen}>
                {t("replay.summary.title")}
            </Button>
            <Drawer
                title={t("replay.summary.title")}
                open={open}
                onClose={() => setOpen(false)}
                width={360}
            >
                {loading || !summary ? (
                    <Spin />
                ) : (
                    <div>
                        <p>{t("replay.summary.adoptionRate")}: {(summary.adoption_rate * 100).toFixed(2)}%</p>
                        {summary.average_sentiment != null && (
                            <p>{t("replay.summary.averageSentiment")}: {typeof summary.average_sentiment === 'number' ? summary.average_sentiment.toFixed(2) : '—'}</p>
                        )}
                        {summary.average_emotion && (
                            <div>
                                <p>{t("replay.summary.averageEmotion")}</p>
                                <ul>
                                    {EMOTION_ORDER.map(k => (
                                        <li key={k}>{emotionLabels[k] ?? k}: {(summary.average_emotion[k] * 100).toFixed(1)}%</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {summary.overall_average_emotion && (
                            <p>{t("replay.summary.overallAverageEmotion")}: {emotionLabels[summary.overall_average_emotion] ?? summary.overall_average_emotion}</p>
                        )}
                        <div>
                            <p>{t("replay.summary.emotionDistribution")}</p>
                            <ul>
                                {(() => {
                                    const total = Object.values(summary.emotion_distribution).reduce((a, b) => a + b, 0);
                                    return EMOTION_ORDER.map(k => {
                                        const v = summary.emotion_distribution[k] ?? 0;
                                        const pct = total ? ((v / total) * 100).toFixed(1) : '0';
                                        return (
                                            <li key={k}>{emotionLabels[k] ?? k}: {v} ({pct}%)</li>
                                        );
                                    });
                                })()}
                            </ul>
                        </div>
                        {summary.analysis_text && (
                            <div>
                                <p>{t("replay.summary.analysisTitle")}</p>
                                <p>{summary.analysis_text}</p>
                            </div>
                        )}
                        <Button onClick={exportJSON} style={{ marginRight: 8 }}>
                            {t("replay.summary.exportJson")}
                        </Button>
                        <Button onClick={exportCSV}>
                            {t("replay.summary.exportCsv")}
                        </Button>
                    </div>
                )}
            </Drawer>
        </>
    );
};

export default ResultSummary;