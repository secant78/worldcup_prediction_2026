# All API keys stored in Secrets Manager — injected into ECS containers at runtime

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name_prefix}/app-secrets"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app_initial" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    # Reddit
    REDDIT_CLIENT_ID     = "REPLACE_ME"
    REDDIT_CLIENT_SECRET = "REPLACE_ME"
    REDDIT_USER_AGENT    = "worldcup-sentiment-tracker/1.0"
    # Football
    FOOTBALL_DATA_API_KEY = "REPLACE_ME"
    API_FOOTBALL_KEY      = "REPLACE_ME"
    # YouTube
    YOUTUBE_API_KEY = "REPLACE_ME"
    # Kafka (populated after MSK is created)
    KAFKA_BOOTSTRAP_SERVERS = ""
    # OpenSearch (populated by opensearch.tf)
    ES_HOST         = ""
    ES_INDEX        = "worldcup-sentiment"
    ES_MATCHES_INDEX = "worldcup-matches"
    # SageMaker
    SAGEMAKER_ENDPOINT_NAME  = var.sagemaker_endpoint_name
    SAGEMAKER_FEATURE_GROUP  = "${local.name_prefix}-team-features"
    BEDROCK_MODEL_ID         = var.bedrock_model_id
    S3_ARTIFACTS_BUCKET      = aws_s3_bucket.artifacts.bucket
    SAGEMAKER_EXECUTION_ROLE = aws_iam_role.sagemaker_execution.arn
    # Redis
    REDIS_HOST = ""  # populated after elasticache is created
  })

  lifecycle { ignore_changes = [secret_string] }
}
