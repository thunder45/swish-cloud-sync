"""Main CDK stack for Cloud Sync Application."""

from aws_cdk import (
    Stack,
    Tags,
    aws_lambda as lambda_,
)
from constructs import Construct
from typing import Optional
from .config import get_config
from .storage_construct import StorageConstruct
from .security_construct import SecurityConstruct
from .vpc_construct import VPCConstruct
from .lambda_construct import LambdaConstruct


class CloudSyncStack(Stack):
    """Cloud Sync Application CDK Stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        **kwargs
    ) -> None:
        """Initialize the Cloud Sync Stack.
        
        Args:
            scope: CDK app scope
            construct_id: Unique identifier for this stack
            environment: Environment name (dev, staging, prod)
            **kwargs: Additional stack properties
        """
        super().__init__(scope, construct_id, **kwargs)

        self.environment = environment
        self.config = get_config(environment)

        # Add tags to all resources
        Tags.of(self).add("Project", "CloudSync")
        Tags.of(self).add("Environment", environment)
        Tags.of(self).add("ManagedBy", "CDK")

        # Create VPC infrastructure (optional, based on config)
        self.vpc: Optional[VPCConstruct] = None
        if self.config.enable_vpc:
            self.vpc = VPCConstruct(
                self,
                "VPC"
            )

        # Create storage infrastructure (DynamoDB and S3)
        self.storage = StorageConstruct(
            self,
            "Storage",
            config=self.config
        )

        # Create security infrastructure (IAM roles)
        self.security = SecurityConstruct(
            self,
            "Security",
            sync_tracker_table=self.storage.sync_tracker_table,
            archive_bucket=self.storage.archive_bucket
        )

        # Create Lambda Layer with shared utilities
        self.lambda_layer = lambda_.LayerVersion(
            self,
            "CloudSyncCommonLayer",
            code=lambda_.Code.from_asset("lambda_layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Shared utilities for Cloud Sync Lambda functions",
        )

        # Create Lambda functions
        self.lambdas = LambdaConstruct(
            self,
            "Lambdas",
            lambda_layer=self.lambda_layer,
            secrets_manager_secret_arn=f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:gopro/credentials-*",
            dynamodb_table_name=self.storage.sync_tracker_table.table_name,
            dynamodb_table_arn=self.storage.sync_tracker_table.table_arn,
            s3_bucket_name=self.storage.archive_bucket.bucket_name,
            s3_bucket_arn=self.storage.archive_bucket.bucket_arn,
            kms_key_arn=self.storage.kms_key.key_arn,
            sns_topic_arn=None,  # Will be added in Phase 5
            vpc=self.vpc.vpc if self.vpc else None,
            vpc_subnets=self.vpc.private_subnets if self.vpc else None,
            security_group=self.vpc.lambda_security_group if self.vpc else None,
        )
