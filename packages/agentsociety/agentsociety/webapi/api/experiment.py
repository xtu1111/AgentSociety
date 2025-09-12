from collections import defaultdict
import csv
import io
import json
import logging
import math
import uuid
import zipfile
from typing import List, cast, Dict, Tuple

import yaml
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import ApiResponseWrapper
from ..models.agent import (
    agent_dialog,
    agent_profile,
    agent_status,
    agent_survey,
    global_prompt,
)
from ..models.experiment import (
    ApiExperiment,
    ApiTime,
    Experiment,
    ExperimentStatus,
    ApiExperimentSummary,
)
from ..models.metric import ApiMetric, metric
from .const import DEMO_USER_ID
from .timezone import ensure_timezone_aware

__all__ = ["router"]

router = APIRouter(tags=["experiments"])


# emotion normalization and scoring
EMOTION_NORMALIZE_MAP = {
    # English
    "interested": "interested",
    "curious": "curious",
    "relaxed": "relaxed",
    "neutral": "neutral",
    "uninterested": "uninterested",
    "skeptical": "skeptical",
    "dislike": "dislike",
    # Japanese
    "興味津々": "interested",
    "好奇心": "curious",
    "リラックス": "relaxed",
    "中立": "neutral",
    "無関心": "uninterested",
    "懐疑的": "skeptical",
    "嫌い": "dislike",
    # Chinese
    "感兴趣": "interested",
    "好奇": "curious",
    "放松": "relaxed",
    "中立": "neutral",
    "不感兴趣": "uninterested",
    "怀疑": "skeptical",
    "讨厌": "dislike",
}

EMOTION_SCORE_MAP = {
    "dislike": -0.6,
    "skeptical": -0.4,
    "uninterested": -0.2,
    "neutral": 0.0,
    "relaxed": 0.2,
    "curious": 0.4,
    "interested": 0.6,
}

# Backward compatibility: older summary code referenced EMOTION_POLARITY.
# Map the name to the current score table so legacy imports still work.
EMOTION_POLARITY = EMOTION_SCORE_MAP

# reverse lookup for mapping numeric emotion scores back to canonical labels
EMOTION_VALUE_TO_LABEL = {v: k for k, v in EMOTION_SCORE_MAP.items()}

async def _find_started_experiment_by_id(
    request: Request, db: AsyncSession, exp_id: uuid.UUID
) -> Experiment:
    """Find an experiment by ID and check if it has started"""
    tenant_id = await request.app.state.get_tenant_id(request)
    stmt = select(Experiment).where(
        Experiment.tenant_id.in_([tenant_id, "", "default"]), Experiment.id == exp_id
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
        )
    exp: Experiment = row[0]
    return exp


@router.get("/experiments")
async def list_experiments(
    request: Request,
) -> ApiResponseWrapper[List[ApiExperiment]]:
    """List all experiments"""
    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        stmt = (
            select(Experiment)
            .where(Experiment.tenant_id.in_([tenant_id, "", "default"]))
            .order_by(Experiment.created_at.desc())
        )
        results = await db.execute(stmt)
        db_experiments = [row[0] for row in results.all() if len(row) > 0]

        # 处理时区
        for experiment in db_experiments:
            experiment.created_at = ensure_timezone_aware(experiment.created_at)
            experiment.updated_at = ensure_timezone_aware(experiment.updated_at)

        experiments = cast(List[ApiExperiment], db_experiments)
        return ApiResponseWrapper(data=experiments)


@router.get("/experiments/{exp_id}")
async def get_experiment_by_id(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[ApiExperiment]:
    """Get experiment by ID"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        stmt = select(Experiment).where(
            Experiment.tenant_id.in_([tenant_id, "", "default"]),
            Experiment.id == exp_id,
        )
        result = await db.execute(stmt)
        row = result.first()
        if not row or len(row) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )
        exp = row[0]
        # 处理时区
        exp.created_at = ensure_timezone_aware(exp.created_at)
        exp.updated_at = ensure_timezone_aware(exp.updated_at)
        return ApiResponseWrapper(data=exp)


@router.get("/experiments/{exp_id}/timeline")
async def get_experiment_status_timeline_by_id(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[List[ApiTime]]:
    """Get experiment status timeline by ID"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        stmt = select(Experiment).where(
            Experiment.tenant_id.in_([tenant_id, "", "default"]),
            Experiment.id == exp_id,
        )
        result = await db.execute(stmt)
        row = result.first()
        if not row or len(row) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )
        experiment: Experiment = row[0]
        # Check if the experiment has started
        if ExperimentStatus(experiment.status) == ExperimentStatus.NOT_STARTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Experiment has not started yet",
            )

        # Get timeline from agent status table
        table_name = experiment.agent_status_tablename

        # the table_name is safe to use in the query
        # it is generated from the experiment id
        query = text(
            f"""
            SELECT day, t 
            FROM {table_name} 
            GROUP BY day, t 
            ORDER BY day, t
        """
        )

        results = (await db.execute(query)).all()
        timeline = [ApiTime(day=int(row[0]), t=float(row[1])) for row in results]

        return ApiResponseWrapper(data=timeline)


@router.get("/experiments/{exp_id}/summary")
async def get_experiment_summary(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[ApiExperimentSummary]:
    """Get experiment summary including adoption rate and emotion stats"""

    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        experiment = await _find_started_experiment_by_id(request, db, exp_id)

        # 如果实验还没开始，直接返回空 summary（统一格式）
        if ExperimentStatus(experiment.status) == ExperimentStatus.NOT_STARTED:
            empty_summary = ApiExperimentSummary(
                adoption_rate=0.0,
                average_sentiment=0.0,
                average_emotion={emo: 0.0 for emo in EMOTION_SCORE_MAP.keys()},
                overall_average_emotion="neutral",
                emotion_distribution={emo: 0 for emo in EMOTION_SCORE_MAP.keys()},
            )
            return ApiResponseWrapper(data=empty_summary)


        table_name = experiment.agent_status_tablename
        status_table, _ = agent_status(table_name)

        subquery = (
            select(
                status_table.c.id,
                status_table.c.status,
                func.row_number()
                .over(
                    partition_by=status_table.c.id,
                    order_by=(status_table.c.day.desc(), status_table.c.t.desc()),
                )
                .label("rn"),
            )
        ).subquery()

        stmt = select(subquery.c.id, subquery.c.status).where(subquery.c.rn == 1)
        try:
            rows = (await db.execute(stmt)).all()
        except Exception:
            logging.warning("status table %s missing", table_name)
            rows = []

        total = len(rows)
        # initialise adoption flags for every agent so the denominator
        # reflects the whole population even if some agents never update
        adopted_flags: Dict[int, bool] = {row.id: False for row in rows}
        sentiments: List[float] = []
        emotion_distribution: Dict[str, int] = defaultdict(int)
        emotion_sums: Dict[str, float] = defaultdict(float)
        emotion_counts: Dict[str, int] = defaultdict(int)

        for row in rows:
            status_data = row.status
            if isinstance(status_data, str):
                try:
                    status_data = json.loads(status_data)
                except Exception:
                    status_data = {}
            if not isinstance(status_data, dict):
                status_data = {}

            adopted_val = status_data.get("adopted")
            if isinstance(adopted_val, (bool, int, float, str)):
                try:
                    adopted_flags[row.id] = (
                        bool(json.loads(str(adopted_val).lower()))
                        if isinstance(adopted_val, str)
                        else bool(adopted_val)
                    )
                except Exception:
                    adopted_flags[row.id] = bool(adopted_val)

            sentiment_val = status_data.get("sentiment")
            if sentiment_val is not None:
                try:
                    sentiments.append(float(sentiment_val))
                except Exception:
                    pass

            emo_val = status_data.get("emotion")
            if isinstance(emo_val, dict):
                for k, v in emo_val.items():
                    try:
                        label = str(k).strip().lower()
                        emotion_sums[label] += float(v)
                        emotion_counts[label] += 1
                    except Exception:
                        pass
            elif isinstance(emo_val, str):
                raw_label = str(emo_val).strip()
                norm_label = EMOTION_NORMALIZE_MAP.get(raw_label, raw_label).lower()
                emotion_distribution[norm_label] += 1

        # Compute adoption and sentiment from metrics if available
        has_metrics, metrics_by_key = await get_experiment_metrics_by_id(
            request, db, exp_id
        )
        if has_metrics:
            for key, metrics in metrics_by_key.items():
                if key.startswith("adopted:"):
                    try:
                        agent_id = int(key.split(":", 1)[1])
                        adopted_flags[agent_id] = bool(metrics[-1].value)
                    except Exception:
                        continue
                elif key.startswith("sentiment:"):
                    try:
                        sentiments.append(metrics[-1].value)
                    except Exception:
                        continue
                elif key.startswith("emotion:"):
                    for m in metrics:
                        try:
                            val = float(m.value)
                        except Exception:
                            continue
                        label = EMOTION_VALUE_TO_LABEL.get(round(val, 1))
                        if label is None:
                            label = str(round(val, 1))
                        label = str(label).strip().lower()
                        emotion_distribution[label] += 1
                        emotion_sums[label] += val
                        emotion_counts[label] += 1

        adoption_rate = (
            sum(1 for v in adopted_flags.values() if v) / len(adopted_flags)
            if adopted_flags
            else 0.0
        )
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        # ---- emotion handling ----
        EMOTION_ORDER = [
            "interested", "curious", "relaxed", "neutral",
            "uninterested", "skeptical", "dislike"
        ]

        # 1) 全程累计的分布 (趋势)
        cumulative_distribution = dict(emotion_distribution)

        # 2) 每个 agent 最后一次状态 → 计算比例 (最终快照)
        final_distribution = {emo: 0 for emo in EMOTION_ORDER}
        for row in rows:
            status_data = row.status
            if isinstance(status_data, str):
                try:
                    status_data = json.loads(status_data)
                except Exception:
                    status_data = {}
            if not isinstance(status_data, dict):
                continue
            emo_val = status_data.get("emotion", "neutral")
            emo_label = str(emo_val).strip().lower()
            if emo_label in final_distribution:
                final_distribution[emo_label] += 1
            else:
                final_distribution["neutral"] += 1  # fallback

        if total > 0:
            average_emotion = {emo: final_distribution[emo] / total for emo in EMOTION_ORDER}
            overall_average_emotion = max(average_emotion, key=average_emotion.get)
        else:
            average_emotion = {emo: 0.0 for emo in EMOTION_ORDER}
            overall_average_emotion = "neutral"

        summary = ApiExperimentSummary(
            adoption_rate=adoption_rate,
            average_sentiment=avg_sentiment,
            average_emotion=average_emotion,                 # 最终快照比例
            overall_average_emotion=overall_average_emotion, # 主导情绪
            emotion_distribution=cumulative_distribution,    # 累计趋势
        )
        return ApiResponseWrapper(data=summary)


@router.delete("/experiments/{exp_id}", status_code=status.HTTP_200_OK)
async def delete_experiment_by_id(
    request: Request,
    exp_id: uuid.UUID,
):
    """Delete experiment by ID"""

    if request.app.state.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Server is in read-only mode"
        )
    tenant_id = await request.app.state.get_tenant_id(request)
    if tenant_id == DEMO_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo user is not allowed to delete experiments",
        )

    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)
        try:
            # Start transaction
            async with db.begin():
                stmt = select(Experiment).where(
                    Experiment.tenant_id == tenant_id, Experiment.id == exp_id
                )
                result = await db.execute(stmt)
                row = result.first()
                if not row or len(row) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Experiment not found",
                    )
                experiment: Experiment = row[0]

                # Get all table names that need to be deleted
                table_names = [
                    experiment.agent_profile_tablename,
                    experiment.agent_status_tablename,
                    experiment.agent_dialog_tablename,
                    experiment.agent_survey_tablename,
                    experiment.global_prompt_tablename,
                    experiment.pending_dialog_tablename,
                    experiment.pending_survey_tablename,
                    experiment.metric_tablename,
                ]

                # Delete related tables
                for table_name in table_names:
                    try:
                        query = text(f"DROP TABLE IF EXISTS {table_name}")
                        await db.execute(query)
                    except Exception as e:
                        logging.error(f"Error dropping table {table_name}: {str(e)}")
                        # Continue execution without interruption

                # Delete experiment record
                await db.delete(experiment)

            # Transaction will be committed automatically

            return ApiResponseWrapper(
                data={"message": "Experiment deleted successfully"}
            )

        except Exception as e:
            logging.error(f"Error deleting experiment: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete experiment: {str(e)}",
            )


async def get_experiment_metrics_by_id(
    request: Request,
    db: AsyncSession,
    exp_id: uuid.UUID,
) -> Tuple[bool, Dict[str, List[ApiMetric]]]:
    """Get metrics for an experiment by ID

    Args:
        request: FastAPI request
        db: Database session
        exp_id: Experiment ID

    Returns:
        Tuple containing:
        - bool: Whether metrics were found
        - Dict[str, List[ApiMetric]]: Metrics data aggregated by key
    """

    experiment = await _find_started_experiment_by_id(request, db, exp_id)

    # Get metrics from the metric table
    table_name = experiment.metric_tablename
    
    # Execute query to get metrics data; when the metrics table doesn't yet
    # exist (e.g. runs with no metrics recorded), return no metrics instead of
    # raising an error so the summary endpoint can still respond.
    query = text(
        f"""
        SELECT key, value, step
        FROM {table_name}
        ORDER BY step
        """
    )
    try:
        results = await db.execute(query)
    except Exception:
        logging.warning("metrics table %s missing", table_name)
        return False, {}

    rows = results.all()

    if not rows:
        return False, {}

    # Aggregate metrics by key, skipping invalid values
    metrics_by_key: Dict[str, List[ApiMetric]] = defaultdict(list)
    for row in rows:
        value = row[1]
        step = row[2]
        if (
            value is None
            or step is None
            or not isinstance(value, (int, float))
            or not isinstance(step, (int, float))
            or not math.isfinite(value)
            or not math.isfinite(step)
        ):
            continue
        api_metric = ApiMetric(
            key=row[0],
            value=float(value),
            step=int(step),
        )
        metrics_by_key[row[0]].append(api_metric)

    return True, metrics_by_key


def serialize_metrics(
    metrics_by_key: Dict[str, List[ApiMetric]],
) -> Dict[str, List[dict]]:
    """Serialize metrics data for JSON output

    Args:
        metrics_by_key: Metrics data aggregated by key

    Returns:
        Dict with serialized metrics data
    """
    return {
        key: [metric.model_dump() for metric in metrics]
        for key, metrics in metrics_by_key.items()
    }


@router.get("/experiments/{exp_id}/metrics")
async def get_experiment_metrics(
    request: Request,
    exp_id: uuid.UUID,
) -> ApiResponseWrapper[Dict[str, List[ApiMetric]]]:
    """Get all metrics for an experiment, aggregated by metric key"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)

        # First verify the experiment exists
        stmt = select(Experiment).where(
            Experiment.tenant_id == tenant_id, Experiment.id == exp_id
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )

        _, metrics_by_key = await get_experiment_metrics_by_id(request, db, exp_id)
        return ApiResponseWrapper(data=metrics_by_key)


@router.post("/experiments/{exp_id}/export")
async def export_experiment_data(
    request: Request,
    exp_id: uuid.UUID,
) -> StreamingResponse:
    """Export experiment data as a zip file containing YAML and CSV files"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)

        # Get experiment info
        stmt = select(Experiment).where(
            Experiment.tenant_id == tenant_id, Experiment.id == exp_id
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )
        experiment: Experiment = row

        # Create in-memory zip file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Export experiment info as YAML
            exp_dict = experiment.to_dict()
            yaml_content = yaml.dump(exp_dict, allow_unicode=True)
            zip_file.writestr("experiment.yaml", yaml_content)

            # Export metrics data as JSON
            found, metrics_by_key = await get_experiment_metrics_by_id(
                request, db, exp_id
            )
            if found:
                serialized_metrics = serialize_metrics(metrics_by_key)
                metrics_json = json.dumps(serialized_metrics, indent=2)
                zip_file.writestr("metrics.json", metrics_json)

            # Export artifacts data
            fs_client = request.app.state.env.fs_client
            artifacts_path = f"exps/{tenant_id}/{exp_id}/artifacts.json"
            artifacts_data = fs_client.download(artifacts_path)
            if artifacts_data:
                zip_file.writestr("artifacts.json", artifacts_data)

            # get all tables
            tables = {
                "agent_profile": agent_profile(experiment.agent_profile_tablename),
                "agent_status": agent_status(experiment.agent_status_tablename),
                "agent_survey": agent_survey(experiment.agent_survey_tablename),
                "agent_dialog": agent_dialog(experiment.agent_dialog_tablename),
                "global_prompt": global_prompt(experiment.global_prompt_tablename),
                "metric": metric(experiment.metric_tablename),
            }

            for table_name, (db_table, columns) in tables.items():
                query = select(db_table)
                results = await db.execute(query)
                rows = results.all()

                if rows:
                    # create csv file
                    output = io.StringIO()
                    writer = csv.writer(output)
                    # write header
                    writer.writerow([col for col in columns])
                    # write data
                    writer.writerows(rows)

                    zip_file.writestr(f"{table_name}.csv", output.getvalue())
                    output.close()

        # prepare response
        zip_buffer.seek(0)
        return StreamingResponse(
            iter([zip_buffer.getvalue()]),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=experiment_{exp_id}_export.zip"
            },
        )


@router.post("/experiments/{exp_id}/artifacts")
async def export_experiment_artifacts(
    request: Request,
    exp_id: uuid.UUID,
) -> StreamingResponse:
    """Export experiment artifacts as a JSON file"""

    tenant_id = await request.app.state.get_tenant_id(request)
    async with request.app.state.get_db() as db:
        db = cast(AsyncSession, db)

        # Get experiment info
        stmt = select(Experiment).where(
            Experiment.tenant_id == tenant_id, Experiment.id == exp_id
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )

        # Get artifacts from S3
        fs_client = request.app.state.env.fs_client
        artifacts_path = f"exps/{tenant_id}/{exp_id}/artifacts.json"
        artifacts_data = fs_client.download(artifacts_path)

        if not artifacts_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Artifacts not found"
            )

        return StreamingResponse(
            iter([artifacts_data]),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=experiment_{exp_id}_artifacts.json"
            },
        )
