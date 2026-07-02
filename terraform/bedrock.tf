# Bedrock is serverless — no infrastructure to provision.
# Access is controlled via IAM (already in iam.tf).
# This file adds CloudWatch monitoring for Bedrock usage.

resource "aws_cloudwatch_log_group" "bedrock" {
  name              = "/aws/bedrock/${var.project}"
  retention_in_days = 30
  tags              = local.tags
}

# CloudWatch dashboard for Bedrock token usage and latency
resource "aws_cloudwatch_dashboard" "bedrock" {
  dashboard_name = "${var.project}-bedrock"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          title  = "Bedrock Input Tokens"
          period = 3600
          stat   = "Sum"
          metrics = [[
            "AWS/Bedrock", "InputTokenCount",
            "ModelId", var.bedrock_model_id
          ]]
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Bedrock Output Tokens"
          period = 3600
          stat   = "Sum"
          metrics = [[
            "AWS/Bedrock", "OutputTokenCount",
            "ModelId", var.bedrock_model_id
          ]]
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Bedrock Invocation Latency (ms)"
          period = 300
          stat   = "p95"
          metrics = [[
            "AWS/Bedrock", "InvocationLatency",
            "ModelId", var.bedrock_model_id
          ]]
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Bedrock Throttles"
          period = 300
          stat   = "Sum"
          metrics = [[
            "AWS/Bedrock", "InvocationThrottles",
            "ModelId", var.bedrock_model_id
          ]]
        }
      },
    ]
  })
}

# Alarm on Bedrock throttling
resource "aws_cloudwatch_metric_alarm" "bedrock_throttles" {
  alarm_name          = "${var.project}-bedrock-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "InvocationThrottles"
  namespace           = "AWS/Bedrock"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Bedrock being throttled — consider reducing request rate"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ModelId = var.bedrock_model_id
  }

  tags = local.tags
}
