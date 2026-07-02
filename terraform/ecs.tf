# ─── ECS Cluster ───────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ─── CloudWatch Log Groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(["consumer", "producer-reddit", "producer-youtube", "producer-football", "streamlit"])
  name              = "/ecs/${local.name_prefix}/${each.key}"
  retention_in_days = 30
}

# ─── Task Definitions ──────────────────────────────────────────────────────────

locals {
  common_env = [
    { name = "AWS_REGION";                value = var.aws_region },
    { name = "SAGEMAKER_ENDPOINT_NAME";   value = var.sagemaker_endpoint_name },
    { name = "BEDROCK_MODEL_ID";          value = var.bedrock_model_id },
    { name = "S3_ARTIFACTS_BUCKET";       value = aws_s3_bucket.artifacts.bucket },
    { name = "SAGEMAKER_EXECUTION_ROLE";  value = aws_iam_role.sagemaker_execution.arn },
    { name = "KAFKA_BOOTSTRAP_SERVERS";   value = aws_msk_cluster.main.bootstrap_brokers },
    { name = "ES_HOST";                   value = "https://${aws_opensearch_domain.main.endpoint}" },
    { name = "ES_INDEX";                  value = "worldcup-sentiment" },
    { name = "ES_MATCHES_INDEX";          value = "worldcup-matches" },
    { name = "REDIS_HOST";                value = aws_elasticache_cluster.redis.cache_nodes[0].address },
  ]

  secrets_from_sm = [
    { name = "REDDIT_CLIENT_ID";      valueFrom = "${aws_secretsmanager_secret.app.arn}:REDDIT_CLIENT_ID::" },
    { name = "REDDIT_CLIENT_SECRET";  valueFrom = "${aws_secretsmanager_secret.app.arn}:REDDIT_CLIENT_SECRET::" },
    { name = "REDDIT_USER_AGENT";     valueFrom = "${aws_secretsmanager_secret.app.arn}:REDDIT_USER_AGENT::" },
    { name = "FOOTBALL_DATA_API_KEY"; valueFrom = "${aws_secretsmanager_secret.app.arn}:FOOTBALL_DATA_API_KEY::" },
    { name = "API_FOOTBALL_KEY";      valueFrom = "${aws_secretsmanager_secret.app.arn}:API_FOOTBALL_KEY::" },
    { name = "YOUTUBE_API_KEY";       valueFrom = "${aws_secretsmanager_secret.app.arn}:YOUTUBE_API_KEY::" },
    { name = "ES_PASSWORD";           valueFrom = "${aws_secretsmanager_secret.app.arn}:OPENSEARCH_PASSWORD::" },
  ]
}

resource "aws_ecs_task_definition" "consumer" {
  family                   = "${local.name_prefix}-consumer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.consumer_cpu
  memory                   = var.consumer_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "consumer"
    image     = "${aws_ecr_repository.services["consumer"].repository_url}:latest"
    essential = true
    environment = local.common_env
    secrets     = local.secrets_from_sm
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["consumer"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "producer_reddit" {
  family                   = "${local.name_prefix}-producer-reddit"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.producer_cpu
  memory                   = var.producer_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name        = "producer-reddit"
    image       = "${aws_ecr_repository.services["producer-reddit"].repository_url}:latest"
    essential   = true
    environment = local.common_env
    secrets     = local.secrets_from_sm
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["producer-reddit"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "producer_youtube" {
  family                   = "${local.name_prefix}-producer-youtube"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.producer_cpu
  memory                   = var.producer_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name        = "producer-youtube"
    image       = "${aws_ecr_repository.services["producer-youtube"].repository_url}:latest"
    essential   = true
    environment = local.common_env
    secrets     = local.secrets_from_sm
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["producer-youtube"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "producer_football" {
  family                   = "${local.name_prefix}-producer-football"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.producer_cpu
  memory                   = var.producer_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name        = "producer-football"
    image       = "${aws_ecr_repository.services["producer-football"].repository_url}:latest"
    essential   = true
    environment = local.common_env
    secrets     = local.secrets_from_sm
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["producer-football"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "streamlit" {
  family                   = "${local.name_prefix}-streamlit"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "streamlit"
    image     = "${aws_ecr_repository.services["streamlit"].repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 8502; protocol = "tcp" }]
    environment = local.common_env
    secrets     = local.secrets_from_sm
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["streamlit"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

# ─── ECS Services ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "consumer" {
  name            = "consumer"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.consumer.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker { enable = true; rollback = true }
}

resource "aws_ecs_service" "producer_reddit" {
  name            = "producer-reddit"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.producer_reddit.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  deployment_circuit_breaker { enable = true; rollback = true }
}

resource "aws_ecs_service" "producer_youtube" {
  name            = "producer-youtube"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.producer_youtube.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  deployment_circuit_breaker { enable = true; rollback = true }
}

resource "aws_ecs_service" "producer_football" {
  name            = "producer-football"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.producer_football.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  deployment_circuit_breaker { enable = true; rollback = true }
}

resource "aws_ecs_service" "streamlit" {
  name            = "streamlit"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.streamlit.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.streamlit.arn
    container_name   = "streamlit"
    container_port   = 8502
  }

  deployment_circuit_breaker { enable = true; rollback = true }
  depends_on = [aws_lb_listener.http]
}
