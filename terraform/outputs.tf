output "dashboard_url" {
  description = "Public URL for the Streamlit dashboard"
  value       = "http://${aws_lb.main.dns_name}"
}

output "opensearch_endpoint" {
  value = "https://${aws_opensearch_domain.main.endpoint}"
}

output "msk_bootstrap_brokers" {
  value = aws_msk_cluster.main.bootstrap_brokers
}

output "redis_endpoint" {
  value = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "ecr_urls" {
  value = { for k, v in aws_ecr_repository.services : k => v.repository_url }
}

output "github_actions_role_arn" {
  description = "Add this to GitHub repo secrets as AWS_ROLE_ARN"
  value       = aws_iam_role.github_actions.arn
}

output "s3_artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "sagemaker_execution_role_arn" {
  value = aws_iam_role.sagemaker_execution.arn
}

output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}
