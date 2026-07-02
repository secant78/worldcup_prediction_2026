# ─── ECS Task execution role (pull images, write logs) ────────────────────────

resource "aws_iam_role" "ecs_execution" {
  name = "${local.name_prefix}-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow"; Principal = { Service = "ecs-tasks.amazonaws.com" }; Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "secrets-read"
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

# ─── ECS Task role (what running containers can do) ────────────────────────────

resource "aws_iam_role" "ecs_task" {
  name = "${local.name_prefix}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow"; Principal = { Service = "ecs-tasks.amazonaws.com" }; Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "app-permissions"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Bedrock
      { Effect = "Allow"; Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]; Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/*" },
      # SageMaker inference
      { Effect = "Allow"; Action = ["sagemaker:InvokeEndpoint"]; Resource = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:endpoint/${var.sagemaker_endpoint_name}" },
      # SageMaker training + management
      { Effect = "Allow"; Action = ["sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:CreateModel", "sagemaker:CreateEndpointConfig", "sagemaker:CreateEndpoint", "sagemaker:UpdateEndpoint", "sagemaker:DescribeEndpoint", "sagemaker:ListTrainingJobs"]; Resource = "*" },
      # Feature Store
      { Effect = "Allow"; Action = ["sagemaker:PutRecord", "sagemaker:GetRecord", "sagemaker:DeleteRecord"]; Resource = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:feature-group/*" },
      # S3
      { Effect = "Allow"; Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]; Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"] },
      # OpenSearch
      { Effect = "Allow"; Action = ["es:ESHttp*"]; Resource = "${aws_opensearch_domain.main.arn}/*" },
      # Secrets
      { Effect = "Allow"; Action = ["secretsmanager:GetSecretValue"]; Resource = aws_secretsmanager_secret.app.arn },
      # CloudWatch metrics
      { Effect = "Allow"; Action = ["cloudwatch:PutMetricData"]; Resource = "*" },
      # Pass role to SageMaker
      { Effect = "Allow"; Action = "iam:PassRole"; Resource = aws_iam_role.sagemaker_execution.arn },
    ]
  })
}

# ─── SageMaker execution role ──────────────────────────────────────────────────

resource "aws_iam_role" "sagemaker_execution" {
  name = "${local.name_prefix}-sagemaker-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow"; Principal = { Service = "sagemaker.amazonaws.com" }; Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "sagemaker_full" {
  role       = aws_iam_role.sagemaker_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
}

resource "aws_iam_role_policy" "sagemaker_s3" {
  name = "s3-access"
  role = aws_iam_role.sagemaker_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
    }]
  })
}

# ─── GitHub Actions OIDC role ──────────────────────────────────────────────────

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_actions" {
  name = "${local.name_prefix}-github-actions"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:*"
        }
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions" {
  name = "deploy-permissions"
  role = aws_iam_role.github_actions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECR push
      { Effect = "Allow"; Action = ["ecr:GetAuthorizationToken"]; Resource = "*" },
      { Effect = "Allow"; Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"]; Resource = [for r in aws_ecr_repository.services : r.arn] },
      # ECS deploy
      { Effect = "Allow"; Action = ["ecs:UpdateService", "ecs:DescribeServices", "ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]; Resource = "*" },
      { Effect = "Allow"; Action = "iam:PassRole"; Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn] },
      # S3 for tfstate
      { Effect = "Allow"; Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]; Resource = ["arn:aws:s3:::worldcup-sentiment-tfstate", "arn:aws:s3:::worldcup-sentiment-tfstate/*"] },
      # DynamoDB for tf lock
      { Effect = "Allow"; Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]; Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/worldcup-sentiment-tflock" },
      # Terraform — full infra management
      { Effect = "Allow"; Action = ["ec2:*", "ecs:*", "ecr:*", "elasticache:*", "es:*", "kafka:*", "iam:*", "logs:*", "secretsmanager:*", "sagemaker:*", "cloudwatch:*"]; Resource = "*" },
    ]
  })
}
