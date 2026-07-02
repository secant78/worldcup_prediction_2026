# Amazon OpenSearch Service — replaces local Elasticsearch
# Same REST API, drop-in replacement, zero code changes needed

resource "aws_opensearch_domain" "main" {
  domain_name    = "${local.name_prefix}"
  engine_version = "OpenSearch_2.11"

  cluster_config {
    instance_type  = var.opensearch_instance_type
    instance_count = var.opensearch_instance_count
  }

  ebs_options {
    ebs_enabled = true
    volume_size = var.opensearch_volume_gb
    volume_type = "gp3"
  }

  vpc_options {
    subnet_ids         = [aws_subnet.private[0].id]
    security_group_ids = [aws_security_group.opensearch.id]
  }

  encrypt_at_rest       { enabled = true }
  node_to_node_encryption { enabled = true }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_security_options {
    enabled                        = true
    anonymous_auth_enabled         = false
    internal_user_database_enabled = true
    master_user_options {
      master_user_name     = "admin"
      master_user_password = random_password.opensearch_admin.result
    }
  }

  log_publishing_options {
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.opensearch.arn
    log_type                 = "INDEX_SLOW_LOGS"
  }

  snapshot_options { automated_snapshot_start_hour = 3 }
}

resource "aws_cloudwatch_log_group" "opensearch" {
  name              = "/aws/opensearch/${local.name_prefix}"
  retention_in_days = 30
}

resource "random_password" "opensearch_admin" {
  length  = 16
  special = true
}

resource "aws_opensearch_domain_policy" "main" {
  domain_name = aws_opensearch_domain.main.domain_name
  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.ecs_task.arn }
      Action    = "es:*"
      Resource  = "${aws_opensearch_domain.main.arn}/*"
    }]
  })
}

# Store credentials in Secrets Manager
resource "aws_secretsmanager_secret_version" "opensearch_creds" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(merge(
    jsondecode(aws_secretsmanager_secret_version.app_initial.secret_string),
    {
      OPENSEARCH_HOST     = "https://${aws_opensearch_domain.main.endpoint}"
      OPENSEARCH_USER     = "admin"
      OPENSEARCH_PASSWORD = random_password.opensearch_admin.result
    }
  ))
  depends_on = [aws_secretsmanager_secret_version.app_initial]
}
