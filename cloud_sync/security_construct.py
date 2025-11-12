"""Security infrastructure construct for Cloud Sync Application."""

from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct
from .config import COMMON_CONFIG


class SecurityConstruct(Construct):
    """Construct for security infrastructure (IAM roles and policies)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        sync_tracker_table: dynamodb.Table,
        archive_bucket: s3.Bucket,
        **kwargs
    ) -> None:
        """Initialize security construct.
        
        Args:
            scope: CDK scope
            construct_id: Construct identifier
            sync_tracker_table: DynamoDB table for sync tracking
            archive_bucket: S3 bucket for video archive
            **kwargs: Additional construct properties
        """
        super().__init__(scope, construct_id, **kwargs)

        self.sync_tracker_table = sync_tracker_table
        self.archive_bucket = archive_bucket

        # Create IAM roles for Lambda functions
        self.media_authenticator_role = self._create_media_authenticator_role()
        self.media_lister_role = self._create_media_lister_role()
        self.video_downloader_role = self._create_video_downloader_role()

        # Create IAM role for Step Functions
        self.orchestrator_role = self._create_orchestrator_role()

    def _create_media_authenticator_role(self) -> iam.Role:
        """Create IAM role for Media Authenticator Lambda.
        
        Returns:
            IAM Role for Media Authenticator
        """
        role = iam.Role(
            self,
            "MediaAuthenticatorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Media Authenticator Lambda function",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                )
            ]
        )

        # Grant Secrets Manager access
        role.add_to_policy(
            iam.PolicyStatement(
                sid="SecretsManagerAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:UpdateSecretValue"
                ],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:"
                    f"secret:{COMMON_CONFIG['secrets_name']}-*"
                ]
            )
        )

        return role

    def _create_media_lister_role(self) -> iam.Role:
        """Create IAM role for Media Lister Lambda.
        
        Returns:
            IAM Role for Media Lister
        """
        role = iam.Role(
            self,
            "MediaListerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Media Lister Lambda function",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                )
            ]
        )

        # Grant DynamoDB read access
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBRead",
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:BatchGetItem",
                    "dynamodb:Query"
                ],
                resources=[
                    self.sync_tracker_table.table_arn,
                    f"{self.sync_tracker_table.table_arn}/index/*"
                ]
            )
        )

        return role

    def _create_video_downloader_role(self) -> iam.Role:
        """Create IAM role for Video Downloader Lambda.
        
        Returns:
            IAM Role for Video Downloader
        """
        role = iam.Role(
            self,
            "VideoDownloaderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Video Downloader Lambda function",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                )
            ]
        )

        # Grant S3 upload access
        role.add_to_policy(
            iam.PolicyStatement(
                sid="S3Upload",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:PutObjectTagging",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                    "s3:GetObject"
                ],
                resources=[
                    f"{self.archive_bucket.bucket_arn}/gopro-videos/*"
                ]
            )
        )

        # Grant DynamoDB write access
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBWrite",
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:UpdateItem",
                    "dynamodb:PutItem",
                    "dynamodb:GetItem"
                ],
                resources=[
                    self.sync_tracker_table.table_arn
                ]
            )
        )

        # Grant CloudWatch Metrics access
        role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchMetrics",
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudwatch:PutMetricData"
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "cloudwatch:namespace": COMMON_CONFIG['cloudwatch_namespace']
                    }
                }
            )
        )

        # Grant KMS access for S3 encryption
        if self.archive_bucket.encryption_key:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="KMSAccess",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kms:Decrypt",
                        "kms:GenerateDataKey"
                    ],
                    resources=[
                        self.archive_bucket.encryption_key.key_arn
                    ]
                )
            )

        return role

    def _create_orchestrator_role(self) -> iam.Role:
        """Create IAM role for Step Functions orchestrator.
        
        Returns:
            IAM Role for Step Functions
        """
        role = iam.Role(
            self,
            "OrchestratorRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            description="Role for Step Functions orchestrator",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                )
            ]
        )

        # Lambda invoke permissions will be added when Lambda functions are created

        # SNS publish permissions will be added when SNS topic is created

        return role
