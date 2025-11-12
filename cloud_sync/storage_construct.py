"""Storage infrastructure construct for Cloud Sync Application."""

from aws_cdk import (
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_kms as kms,
    RemovalPolicy,
    Duration,
)
from constructs import Construct
from .config import EnvironmentConfig, COMMON_CONFIG


class StorageConstruct(Construct):
    """Construct for storage infrastructure (DynamoDB and S3)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        **kwargs
    ) -> None:
        """Initialize storage construct.
        
        Args:
            scope: CDK scope
            construct_id: Construct identifier
            config: Environment configuration
            **kwargs: Additional construct properties
        """
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        # Create DynamoDB table for sync tracking
        self.sync_tracker_table = self._create_dynamodb_table()

        # Create S3 bucket for video archive
        self.archive_bucket = self._create_s3_bucket()

    def _create_dynamodb_table(self) -> dynamodb.Table:
        """Create DynamoDB table for sync state tracking.
        
        Returns:
            DynamoDB Table construct
        """
        table = dynamodb.Table(
            self,
            "SyncTrackerTable",
            table_name=f"{COMMON_CONFIG['dynamodb_table_name']}-{self.config.name}",
            partition_key=dynamodb.Attribute(
                name="media_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST
            if self.config.dynamodb_billing_mode == 'PAY_PER_REQUEST'
            else dynamodb.BillingMode.PROVISIONED,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.RETAIN if self.config.name == 'prod'
            else RemovalPolicy.DESTROY,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )

        # Add Global Secondary Index for status queries
        table.add_global_secondary_index(
            index_name="status-sync_timestamp-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sync_timestamp",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )

        return table

    def _create_s3_bucket(self) -> s3.Bucket:
        """Create S3 bucket for video archive with lifecycle policies.
        
        Returns:
            S3 Bucket construct
        """
        # Create KMS key for S3 encryption
        kms_key = kms.Key(
            self,
            "ArchiveBucketKey",
            description=f"KMS key for Cloud Sync archive bucket - {self.config.name}",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN if self.config.name == 'prod'
            else RemovalPolicy.DESTROY
        )

        # Create S3 bucket
        bucket = s3.Bucket(
            self,
            "ArchiveBucket",
            bucket_name=f"gopro-archive-bucket-{self.config.name}-{self.node.addr}",
            versioned=True,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN if self.config.name == 'prod'
            else RemovalPolicy.DESTROY,
            auto_delete_objects=False if self.config.name == 'prod' else True,
        )

        # Add lifecycle rules for cost optimization
        bucket.add_lifecycle_rule(
            id="transition-to-deep-archive",
            enabled=True,
            prefix="gopro-videos/",
            transitions=[
                s3.Transition(
                    storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                    transition_after=Duration.days(
                        self.config.s3_lifecycle_glacier_ir_days
                    )
                ),
                s3.Transition(
                    storage_class=s3.StorageClass.DEEP_ARCHIVE,
                    transition_after=Duration.days(
                        self.config.s3_lifecycle_standard_days +
                        self.config.s3_lifecycle_glacier_ir_days
                    )
                )
            ],
            noncurrent_version_transitions=[
                s3.NoncurrentVersionTransition(
                    storage_class=s3.StorageClass.DEEP_ARCHIVE,
                    transition_after=Duration.days(30)
                )
            ]
        )

        # Add bucket policy to deny insecure transport
        bucket.add_to_resource_policy(
            s3.PolicyStatement(
                sid="DenyInsecureTransport",
                effect=s3.Effect.DENY,
                principals=[s3.AnyPrincipal()],
                actions=["s3:*"],
                resources=[
                    bucket.bucket_arn,
                    f"{bucket.bucket_arn}/*"
                ],
                conditions={
                    "Bool": {
                        "aws:SecureTransport": "false"
                    }
                }
            )
        )

        return bucket
