# ─── SageMaker Feature Store ───────────────────────────────────────────────────

resource "aws_sagemaker_feature_group" "team_features" {
  feature_group_name             = "${var.project}-team-features"
  record_identifier_feature_name = "team_name"
  event_time_feature_name        = "event_time"
  role_arn                       = aws_iam_role.sagemaker_execution.arn

  feature_definition {
    feature_name = "team_name"
    feature_type = "String"
  }
  feature_definition {
    feature_name = "event_time"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "form_score"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "avg_goals_scored"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "avg_goals_conceded"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "goal_difference"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "clean_sheet_rate"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "comeback_wins"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "xg"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "xga"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "xg_difference"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "avg_possession"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "shot_accuracy"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "avg_pass_accuracy"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "ppda"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "avg_sentiment"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "positive_ratio"
    feature_type = "Fractional"
  }
  feature_definition {
    feature_name = "matches_played"
    feature_type = "Fractional"
  }

  online_store_config {
    enable_online_store = true
  }

  offline_store_config {
    s3_storage_config {
      s3_uri = "s3://${aws_s3_bucket.artifacts.bucket}/feature-store/"
    }
    disable_glue_table_creation = false
  }

  tags = local.tags
}

# ─── SageMaker Model Registry ──────────────────────────────────────────────────

resource "aws_sagemaker_model_package_group" "win_probability" {
  model_package_group_name        = "${var.project}-win-probability"
  model_package_group_description = "World Cup win probability XGBoost models"
  tags                            = local.tags
}

# ─── CloudWatch dashboard for SageMaker endpoint monitoring ───────────────────

resource "aws_cloudwatch_dashboard" "sagemaker" {
  dashboard_name = "${var.project}-sagemaker"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          title  = "Endpoint Invocations"
          period = 300
          stat   = "Sum"
          metrics = [[
            "AWS/SageMaker", "Invocations",
            "EndpointName", var.sagemaker_endpoint_name,
            "VariantName", "AllTraffic"
          ]]
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Model Latency (ms)"
          period = 300
          stat   = "p99"
          metrics = [[
            "AWS/SageMaker", "ModelLatency",
            "EndpointName", var.sagemaker_endpoint_name,
            "VariantName", "AllTraffic"
          ]]
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Invocation Errors"
          period = 300
          stat   = "Sum"
          metrics = [[
            "AWS/SageMaker", "Invocation4XXErrors",
            "EndpointName", var.sagemaker_endpoint_name,
            "VariantName", "AllTraffic"
          ]]
        }
      },
    ]
  })
}

# ─── CloudWatch alarm — endpoint errors ───────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "endpoint_errors" {
  alarm_name          = "${var.project}-endpoint-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Invocation5XXErrors"
  namespace           = "AWS/SageMaker"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "SageMaker endpoint returning 5XX errors"
  treat_missing_data  = "notBreaching"

  dimensions = {
    EndpointName = var.sagemaker_endpoint_name
    VariantName  = "AllTraffic"
  }

  tags = local.tags
}
