"""Main CDK stack for Cloud Sync Application."""

from aws_cdk import (
    Stack,
    Tags,
)
from constructs import Construct
from typing import Optional
from .config import get_config
from .storage_construct import StorageConstruct
from .security_construct import SecurityConstruct
from .vpc_construct import VPCConstruct


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
