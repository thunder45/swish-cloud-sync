"""Configuration management for Cloud Sync Application."""

from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class EnvironmentConfig:
    """Environment-specific configuration."""
    
    # Environment name
    name: str
    
    # Lambda configuration
    lambda_memory_mb: int
    lambda_timeout_seconds: int
    lambda_reserved_concurrency: int
    
    # DynamoDB configuration
    dynamodb_billing_mode: str  # 'PAY_PER_REQUEST' or 'PROVISIONED'
    
    # S3 configuration
    s3_lifecycle_standard_days: int
    s3_lifecycle_glacier_ir_days: int
    s3_lifecycle_deep_archive_days: int
    
    # Step Functions configuration
    step_functions_timeout_seconds: int
    step_functions_max_concurrency: int
    
    # Monitoring configuration
    enable_xray: bool
    log_retention_days: int
    
    # VPC configuration
    enable_vpc: bool
    
    # Cost optimization
    enable_s3_intelligent_tiering: bool


# Environment configurations
ENVIRONMENTS: Dict[str, EnvironmentConfig] = {
    'dev': EnvironmentConfig(
        name='dev',
        lambda_memory_mb=512,
        lambda_timeout_seconds=900,  # 15 minutes
        lambda_reserved_concurrency=2,
        dynamodb_billing_mode='PAY_PER_REQUEST',
        s3_lifecycle_standard_days=7,
        s3_lifecycle_glacier_ir_days=7,
        s3_lifecycle_deep_archive_days=97,  # Must be 90+ days after GLACIER_IR (7+90=97)
        step_functions_timeout_seconds=7200,  # 2 hours
        step_functions_max_concurrency=2,
        enable_xray=True,
        log_retention_days=7,
        enable_vpc=False,  # Disable VPC in dev to save costs
        enable_s3_intelligent_tiering=False
    ),
    'staging': EnvironmentConfig(
        name='staging',
        lambda_memory_mb=1024,
        lambda_timeout_seconds=900,
        lambda_reserved_concurrency=5,
        dynamodb_billing_mode='PAY_PER_REQUEST',
        s3_lifecycle_standard_days=7,
        s3_lifecycle_glacier_ir_days=7,
        s3_lifecycle_deep_archive_days=97,  # Must be 90+ days after GLACIER_IR (7+90=97)
        step_functions_timeout_seconds=7200,
        step_functions_max_concurrency=5,
        enable_xray=True,
        log_retention_days=30,
        enable_vpc=True,
        enable_s3_intelligent_tiering=False
    ),
    'prod': EnvironmentConfig(
        name='prod',
        lambda_memory_mb=1024,
        lambda_timeout_seconds=900,
        lambda_reserved_concurrency=10,
        dynamodb_billing_mode='PAY_PER_REQUEST',
        s3_lifecycle_standard_days=7,
        s3_lifecycle_glacier_ir_days=7,
        s3_lifecycle_deep_archive_days=97,  # Must be 90+ days after GLACIER_IR (7+90=97)
        step_functions_timeout_seconds=43200,  # 12 hours
        step_functions_max_concurrency=5,
        enable_xray=True,
        log_retention_days=30,
        enable_vpc=True,
        enable_s3_intelligent_tiering=False
    )
}


def get_config(environment: str) -> EnvironmentConfig:
    """Get configuration for environment.
    
    Args:
        environment: Environment name (dev, staging, prod)
        
    Returns:
        EnvironmentConfig for the environment
        
    Raises:
        ValueError: If environment not found
    """
    if environment not in ENVIRONMENTS:
        raise ValueError(
            f"Unknown environment: {environment}. "
            f"Valid environments: {list(ENVIRONMENTS.keys())}"
        )
    return ENVIRONMENTS[environment]


# Common configuration (not environment-specific)
COMMON_CONFIG = {
    'project_name': 'CloudSync',
    'dynamodb_table_name': 'gopro-sync-tracker',
    'secrets_name': 'gopro/credentials',
    'sns_topic_name': 'gopro-sync-alerts',
    'cloudwatch_namespace': 'GoProSync',
    'multipart_threshold_bytes': 104857600,  # 100 MB
    'chunk_size_bytes': 104857600,  # 100 MB
    'page_size': 100,
    'max_videos': 1000,
    'token_expiry_hours': 24
}
