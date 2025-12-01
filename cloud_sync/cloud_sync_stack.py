"""Main CDK stack for Cloud Sync Application."""

from aws_cdk import (
    Stack,
    Tags,
    CfnOutput,
    Duration,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_sqs as sqs,
)
from constructs import Construct
from typing import Optional
from .config import get_config
from .storage_construct import StorageConstruct
from .security_construct import SecurityConstruct
from .vpc_construct import VPCConstruct
from .lambda_construct import LambdaConstruct
from .orchestration_construct import OrchestrationConstruct
from .monitoring_construct import MonitoringConstruct
from .secrets_rotation_construct import SecretsRotationConstruct


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

        # Create SNS topic for alerts (Phase 5)
        self.sns_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=f"{environment}-gopro-sync-alerts",
            display_name="GoPro Sync Alerts",
            master_key=None,  # Use AWS managed key for encryption
        )

        # Add email subscription (configure via environment variable or parameter)
        # Uncomment and set email when deploying:
        # self.sns_topic.add_subscription(
        #     sns_subscriptions.EmailSubscription("ops-team@company.com")
        # )

        # Create Dead Letter Queues for Lambda functions (Phase 5)
        self.dlqs = {
            "media-authenticator": sqs.Queue(
                self,
                "MediaAuthenticatorDLQ",
                queue_name=f"{environment}-media-authenticator-dlq",
                retention_period=Duration.days(14),
            ),
            "media-lister": sqs.Queue(
                self,
                "MediaListerDLQ",
                queue_name=f"{environment}-media-lister-dlq",
                retention_period=Duration.days(14),
            ),
            "video-downloader": sqs.Queue(
                self,
                "VideoDownloaderDLQ",
                queue_name=f"{environment}-video-downloader-dlq",
                retention_period=Duration.days(14),
            ),
        }

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
            sns_topic_arn=self.sns_topic.topic_arn,
            vpc=self.vpc.vpc if self.vpc else None,
            vpc_subnets=self.vpc.private_subnets if self.vpc else None,
            security_group=self.vpc.lambda_security_group if self.vpc else None,
        )

        # Note: DLQs are created and monitored via CloudWatch alarms.
        # Step Functions handles retries for synchronous Lambda invocations.
        # DLQs would be used for async invocations (e.g., SNS triggers, EventBridge)
        # if added in the future.

        # Create Step Functions orchestration
        self.orchestration = OrchestrationConstruct(
            self,
            "Orchestration",
            media_authenticator=self.lambdas.media_authenticator,
            media_lister=self.lambdas.media_lister,
            video_downloader=self.lambdas.video_downloader,
            sns_topic=self.sns_topic,
        )

        # Create monitoring infrastructure (Phase 5)
        self.monitoring = MonitoringConstruct(
            self,
            "Monitoring",
            sns_topic=self.sns_topic,
            lambda_functions={
                "media-authenticator": self.lambdas.media_authenticator,
                "media-lister": self.lambdas.media_lister,
                "video-downloader": self.lambdas.video_downloader,
            },
            state_machine=self.orchestration.state_machine,
            dlqs=self.dlqs,
            environment=environment,
        )

        # Create secrets rotation infrastructure (Phase 6)
        self.secrets_rotation = SecretsRotationConstruct(
            self,
            "SecretsRotation",
            secret_name="gopro/credentials",
            provider_name="gopro",
            sns_topic_arn=self.sns_topic.topic_arn,
            lambda_layer=self.lambda_layer,
            vpc=self.vpc.vpc if self.vpc else None,
            security_group=self.vpc.lambda_security_group if self.vpc else None,
        )
        
        # Add rotation DLQ to monitoring
        self.dlqs["secrets-rotator"] = self.secrets_rotation.dlq

        # Stack outputs for observability and operations
        CfnOutput(
            self,
            "StateMachineArn",
            value=self.orchestration.state_machine.state_machine_arn,
            description="GoPro Sync State Machine ARN",
            export_name=f"{environment}-gopro-sync-state-machine-arn",
        )

        CfnOutput(
            self,
            "StateMachineConsoleUrl",
            value=(
                f"https://{self.region}.console.aws.amazon.com/states/home"
                f"?region={self.region}#/statemachines/view/"
                f"{self.orchestration.state_machine.state_machine_arn}"
            ),
            description="State Machine Console URL",
        )

        CfnOutput(
            self,
            "EventBridgeRuleName",
            value=self.orchestration.scheduler_rule.rule_name,
            description="EventBridge Daily Schedule Rule Name",
        )

        CfnOutput(
            self,
            "DynamoDBTableName",
            value=self.storage.sync_tracker_table.table_name,
            description="DynamoDB Sync Tracker Table",
            export_name=f"{environment}-sync-tracker-table",
        )

        CfnOutput(
            self,
            "S3BucketName",
            value=self.storage.archive_bucket.bucket_name,
            description="S3 Archive Bucket",
            export_name=f"{environment}-archive-bucket",
        )

        CfnOutput(
            self,
            "SNSTopicArn",
            value=self.sns_topic.topic_arn,
            description="SNS Alert Topic ARN",
            export_name=f"{environment}-alert-topic-arn",
        )

        CfnOutput(
            self,
            "CloudWatchDashboardUrl",
            value=(
                f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
                f"?region={self.region}#dashboards:name={environment}-GoPro-Sync-Operations"
            ),
            description="CloudWatch Dashboard URL",
        )

        CfnOutput(
            self,
            "SecretsRotatorFunctionName",
            value=self.secrets_rotation.rotator_function.function_name,
            description="Secrets Rotator Lambda Function Name",
            export_name=f"{environment}-secrets-rotator-function",
        )
