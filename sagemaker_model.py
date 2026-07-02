"""
SageMaker XGBoost win probability model.

Handles:
  - Training XGBoost on SageMaker (replaces local sklearn LogReg)
  - Deploying/updating a real-time endpoint
  - Predicting via the endpoint
  - Pushing features to SageMaker Feature Store
  - Registering models in the Model Registry
"""
import io
import json
import os
import time
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_ARTIFACTS_BUCKET", "")
EXECUTION_ROLE = os.getenv("SAGEMAKER_EXECUTION_ROLE", "")
ENDPOINT_NAME = os.getenv("SAGEMAKER_ENDPOINT_NAME", "worldcup-win-probability")
FEATURE_GROUP = os.getenv("SAGEMAKER_FEATURE_GROUP", "worldcup-sentiment-team-features")
MODEL_PACKAGE_GROUP = "worldcup-sentiment-win-probability"

XGB_IMAGE = f"683313688378.dkr.ecr.{AWS_REGION}.amazonaws.com/sagemaker-xgboost:1.7-1"


def _sm_client():
    return boto3.client("sagemaker", region_name=AWS_REGION)


def _sm_runtime():
    return boto3.client("sagemaker-runtime", region_name=AWS_REGION)


def _s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def _featurestore_runtime():
    return boto3.client("sagemaker-featurestore-runtime", region_name=AWS_REGION)


# ─── Feature Store ─────────────────────────────────────────────────────────────

def push_team_features(team_features: dict, team_sentiment: dict):
    """Push current team features to SageMaker Feature Store for versioning."""
    client = _featurestore_runtime()
    now = datetime.now(timezone.utc).timestamp()

    for team, tf in team_features.items():
        ts = team_sentiment.get(team, {})
        record = [
            {"FeatureName": "team_name",          "ValueAsString": team},
            {"FeatureName": "event_time",          "ValueAsString": str(now)},
            {"FeatureName": "form_score",          "ValueAsString": str(tf.get("form_score", 0))},
            {"FeatureName": "avg_goals_scored",    "ValueAsString": str(tf.get("avg_goals_scored", 0))},
            {"FeatureName": "avg_goals_conceded",  "ValueAsString": str(tf.get("avg_goals_conceded", 0))},
            {"FeatureName": "goal_difference",     "ValueAsString": str(tf.get("goal_difference", 0))},
            {"FeatureName": "clean_sheet_rate",    "ValueAsString": str(tf.get("clean_sheet_rate", 0))},
            {"FeatureName": "comeback_wins",       "ValueAsString": str(tf.get("comeback_wins", 0))},
            {"FeatureName": "xg",                  "ValueAsString": str(tf.get("xg", 0))},
            {"FeatureName": "xga",                 "ValueAsString": str(tf.get("xga", 0))},
            {"FeatureName": "xg_difference",       "ValueAsString": str(tf.get("xg_difference", 0))},
            {"FeatureName": "avg_possession",      "ValueAsString": str(tf.get("avg_possession", 0))},
            {"FeatureName": "shot_accuracy",       "ValueAsString": str(tf.get("shot_accuracy", 0))},
            {"FeatureName": "avg_pass_accuracy",   "ValueAsString": str(tf.get("avg_pass_accuracy", 0))},
            {"FeatureName": "ppda",                "ValueAsString": str(tf.get("ppda", 0))},
            {"FeatureName": "avg_sentiment",       "ValueAsString": str(ts.get("avg_sentiment", 0.5))},
            {"FeatureName": "positive_ratio",      "ValueAsString": str(ts.get("positive_ratio", 0))},
            {"FeatureName": "matches_played",      "ValueAsString": str(tf.get("matches_played", 0))},
        ]
        try:
            client.put_record(FeatureGroupName=FEATURE_GROUP, Record=record)
        except Exception as e:
            print(f"[featurestore] Error pushing {team}: {e}")

    print(f"[featurestore] Pushed features for {len(team_features)} teams.")


# ─── Training ──────────────────────────────────────────────────────────────────

def _build_training_csv(finished: list[dict], team_features: dict, team_sentiment: dict) -> pd.DataFrame:
    """Build training dataset from finished matches."""
    from win_probability import build_feature_vector, encode_outcome
    rows = []
    for m in finished:
        home = m.get("home_team")
        away = m.get("away_team")
        label = encode_outcome(m)
        if not home or not away or label is None:
            continue
        vec = build_feature_vector(home, away, team_features, team_sentiment)
        rows.append([label] + vec.tolist())

    from win_probability import FEATURE_NAMES
    cols = ["label"] + FEATURE_NAMES
    return pd.DataFrame(rows, columns=cols)


def train_on_sagemaker(finished: list[dict], team_features: dict, team_sentiment: dict) -> str:
    """
    Upload training data to S3 and launch a SageMaker XGBoost training job.
    Returns the training job name.
    """
    df = _build_training_csv(finished, team_features, team_sentiment)
    if len(df) < 5:
        raise ValueError("Need at least 5 finished matches to train.")

    # Upload training CSV to S3
    job_name = f"worldcup-win-prob-{int(time.time())}"
    s3_key = f"training-data/{job_name}/train.csv"
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, header=False)
    _s3_client().put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=csv_buffer.getvalue().encode("utf-8"),
    )
    s3_train_uri = f"s3://{S3_BUCKET}/training-data/{job_name}/"
    s3_output_uri = f"s3://{S3_BUCKET}/training-output/{job_name}/"

    print(f"[sagemaker] Launching training job: {job_name}")
    print(f"[sagemaker] Training data: {s3_train_uri} ({len(df)} rows)")

    _sm_client().create_training_job(
        TrainingJobName=job_name,
        AlgorithmSpecification={
            "TrainingImage": XGB_IMAGE,
            "TrainingInputMode": "File",
        },
        RoleArn=EXECUTION_ROLE,
        InputDataConfig=[{
            "ChannelName": "train",
            "DataSource": {
                "S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": s3_train_uri,
                    "S3DataDistributionType": "FullyReplicated",
                }
            },
            "ContentType": "text/csv",
        }],
        OutputDataConfig={"S3OutputPath": s3_output_uri},
        ResourceConfig={
            "InstanceType": os.getenv("SM_TRAINING_INSTANCE", "ml.m5.xlarge"),
            "InstanceCount": 1,
            "VolumeSizeInGB": 10,
        },
        HyperParameters={
            "objective":        "multi:softprob",
            "num_class":        "3",
            "num_round":        "200",
            "max_depth":        "4",
            "eta":              "0.1",
            "subsample":        "0.8",
            "colsample_bytree": "0.8",
            "eval_metric":      "mlogloss",
        },
        StoppingCondition={"MaxRuntimeInSeconds": 1800},
        Tags=[{"Key": "Project", "Value": "worldcup-sentiment"}],
    )
    return job_name


def wait_for_training(job_name: str, poll_seconds: int = 30) -> str:
    """Poll until training job completes. Returns S3 URI of model artifact."""
    print(f"[sagemaker] Waiting for training job {job_name}...")
    sm = _sm_client()
    while True:
        resp = sm.describe_training_job(TrainingJobName=job_name)
        status = resp["TrainingJobStatus"]
        print(f"[sagemaker]   status: {status}")
        if status == "Completed":
            return resp["ModelArtifacts"]["S3ModelArtifacts"]
        if status in ("Failed", "Stopped"):
            reason = resp.get("FailureReason", "unknown")
            raise RuntimeError(f"Training job {job_name} {status}: {reason}")
        time.sleep(poll_seconds)


# ─── Deployment ────────────────────────────────────────────────────────────────

def deploy_endpoint(model_artifact_s3: str, job_name: str):
    """Create or update the SageMaker real-time endpoint."""
    sm = _sm_client()
    model_name = job_name
    config_name = f"{job_name}-config"

    # Create model
    sm.create_model(
        ModelName=model_name,
        PrimaryContainer={
            "Image": XGB_IMAGE,
            "ModelDataUrl": model_artifact_s3,
            "Environment": {"SAGEMAKER_PROGRAM": "inference.py"},
        },
        ExecutionRoleArn=EXECUTION_ROLE,
    )

    # Create endpoint config
    sm.create_endpoint_config(
        EndpointConfigName=config_name,
        ProductionVariants=[{
            "VariantName": "AllTraffic",
            "ModelName": model_name,
            "InitialInstanceCount": 1,
            "InstanceType": os.getenv("SM_ENDPOINT_INSTANCE", "ml.t3.medium"),
            "InitialVariantWeight": 1.0,
        }],
    )

    # Create or update endpoint
    try:
        sm.describe_endpoint(EndpointName=ENDPOINT_NAME)
        print(f"[sagemaker] Updating endpoint {ENDPOINT_NAME}...")
        sm.update_endpoint(EndpointName=ENDPOINT_NAME, EndpointConfigName=config_name)
    except sm.exceptions.ClientError:
        print(f"[sagemaker] Creating endpoint {ENDPOINT_NAME}...")
        sm.create_endpoint(EndpointName=ENDPOINT_NAME, EndpointConfigName=config_name)

    print(f"[sagemaker] Endpoint {ENDPOINT_NAME} deploying (takes ~5 min)...")


def register_model(model_artifact_s3: str, metrics: dict = None):
    """Register model version in SageMaker Model Registry."""
    sm = _sm_client()
    approval = "Approved" if (metrics or {}).get("accuracy", 0) > 0.5 else "PendingManualApproval"
    kwargs = {
        "ModelPackageGroupName": MODEL_PACKAGE_GROUP,
        "ModelPackageDescription": f"Win probability XGBoost trained {datetime.now().date()}",
        "InferenceSpecification": {
            "Containers": [{
                "Image": XGB_IMAGE,
                "ModelDataUrl": model_artifact_s3,
            }],
            "SupportedContentTypes": ["text/csv"],
            "SupportedResponseMIMETypes": ["application/json"],
        },
        "ModelApprovalStatus": approval,
    }
    if metrics:
        kwargs["ModelMetrics"] = {
            "ModelQuality": {
                "Statistics": {
                    "ContentType": "application/json",
                    "S3Uri": "",  # placeholder
                }
            }
        }
    resp = sm.create_model_package(**kwargs)
    print(f"[sagemaker] Registered model: {resp['ModelPackageArn']}")
    return resp["ModelPackageArn"]


# ─── Inference ─────────────────────────────────────────────────────────────────

def predict_via_endpoint(feature_vector: np.ndarray) -> dict:
    """
    Call the SageMaker endpoint for win probability prediction.
    Returns {home_win: %, draw: %, away_win: %}.
    """
    runtime = _sm_runtime()
    csv_row = ",".join(str(x) for x in feature_vector.tolist())
    response = runtime.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="text/csv",
        Body=csv_row,
    )
    probs = json.loads(response["Body"].read())

    # XGBoost softprob returns [p_away, p_draw, p_home] for classes [0,1,2]
    if isinstance(probs, list) and len(probs) == 3:
        p_away, p_draw, p_home = probs
    else:
        p_home, p_draw, p_away = 0.33, 0.34, 0.33

    return {
        "home_win": round(p_home * 100, 1),
        "draw":     round(p_draw * 100, 1),
        "away_win": round(p_away * 100, 1),
        "source":   "sagemaker",
    }


def endpoint_is_available() -> bool:
    """Check if the SageMaker endpoint exists and is InService."""
    try:
        resp = _sm_client().describe_endpoint(EndpointName=ENDPOINT_NAME)
        return resp["EndpointStatus"] == "InService"
    except Exception:
        return False
