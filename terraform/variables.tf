variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "worldcup-sentiment"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.10.0/24", "10.0.11.0/24"]
}

# ECS
variable "consumer_cpu"    { type = number; default = 1024 }
variable "consumer_memory" { type = number; default = 4096 }  # needs RAM for NLP models
variable "app_cpu"         { type = number; default = 512 }
variable "app_memory"      { type = number; default = 1024 }
variable "producer_cpu"    { type = number; default = 256 }
variable "producer_memory" { type = number; default = 512 }

# OpenSearch
variable "opensearch_instance_type"  { type = string; default = "t3.small.search" }
variable "opensearch_instance_count" { type = number; default = 1 }
variable "opensearch_volume_gb"      { type = number; default = 20 }

# MSK
variable "msk_instance_type" { type = string; default = "kafka.t3.small" }

# ElastiCache
variable "redis_node_type" { type = string; default = "cache.t3.micro" }

# SageMaker
variable "sagemaker_endpoint_name"         { type = string; default = "worldcup-win-probability" }
variable "sagemaker_training_instance"     { type = string; default = "ml.m5.xlarge" }
variable "sagemaker_endpoint_instance"     { type = string; default = "ml.t3.medium" }

# Bedrock
variable "bedrock_model_id" { type = string; default = "anthropic.claude-haiku-4-5" }

# GitHub
variable "github_repo" {
  type        = string
  description = "GitHub repo in format owner/repo"
  default     = "secant78/worldcup-sentiment"
}
