"""
Lambda Functions Construct

Creates and configures all Lambda functions for the Cloud Sync Application.
"""

from aws_cdk import (
    Duration,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    aws_ec2 as ec2,
)
from constructs import Construct
from typing import Optional


class LambdaConstruct(Construct):
    """Construct for Lambda functions"""
    
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        lambda_layer: lambda_.LayerVersion,
        secrets_manager_secret_arn: str,
        dynamodb_table_name: str,
        dynamodb_table_arn: str,
        s3_bucket_name: str,
        s3_bucket_arn: str,
        kms_key_arn: str,
        sns_topic_arn: Optional[str] = None,
        vpc: Optional[ec2.IVpc] = None,
        vpc_subnets: Optional[ec2.SubnetSelection] = None,
        security_group: Optional[ec2.ISecurityGroup] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)
        
        self.lambda_layer = lambda_layer
        self.secrets_manager_secret_arn = secrets_manager_secret_arn
        self.dynamodb_table_name = dynamodb_table_name
        self.dynamodb_table_arn = dynamodb_table_arn
        self.s3_bucket_name = s3_bucket_name
        self.s3_bucket_arn = s3_bucket_arn
        self.kms_key_arn = kms_key_arn
        self.sns_topic_arn = sns_topic_arn
        self.vpc = vpc
        self.vpc_subnets = vpc_subnets
        self.security_group = security_group
        
        # Create Lambda functions
        self.token_validator = self._create_token_validator()
        self.media_authenticator = self._create_media_authenticator()
        self.media_lister = self._create_media_lister()
        self.video_downloader = self._create_video_downloader()
    
    def _create_token_validator(self) -> lambda_.Function:
        """Create Token Validator Lambda function"""
        
        # Create IAM role
        role = iam.Role(
            self, "TokenValidatorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Token Validator Lambda function",
        )
        
        # Add Secrets Manager read-only permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="SecretsManagerReadAccess",
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[self.secrets_manager_secret_arn],
        ))
        
        # Add CloudWatch Logs permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogs",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=["*"],
        ))
        
        # Add CloudWatch Metrics permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchMetrics",
            effect=iam.Effect.ALLOW,
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "cloudwatch:namespace": "CloudSync/TokenValidation"
                }
            },
        ))
        
        # Add X-Ray permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="XRayTracing",
            effect=iam.Effect.ALLOW,
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
            ],
            resources=["*"],
        ))
        
        # Add SNS permissions if topic is configured
        if self.sns_topic_arn:
            role.add_to_policy(iam.PolicyStatement(
                sid="SNSPublish",
                effect=iam.Effect.ALLOW,
                actions=["sns:Publish"],
                resources=[self.sns_topic_arn],
            ))
        
        # Add VPC permissions if VPC is enabled
        if self.vpc:
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            )
        
        # Create Lambda function
        function = lambda_.Function(
            self, "TokenValidator",
            function_name="token-validator",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda_functions/token_validator"),
            role=role,
            layers=[self.lambda_layer],
            memory_size=256,
            timeout=Duration.seconds(30),
            environment={
                "SECRET_NAME": "gopro/credentials",
                "SNS_TOPIC_ARN": self.sns_topic_arn or "",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            vpc=self.vpc,
            vpc_subnets=self.vpc_subnets,
            security_groups=[self.security_group] if self.security_group else None,
        )
        
        return function
    
    def _create_media_authenticator(self) -> lambda_.Function:
        """Create Media Authenticator Lambda function"""
        
        # Create IAM role
        role = iam.Role(
            self, "MediaAuthenticatorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Media Authenticator Lambda function",
        )
        
        # Add Secrets Manager permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="SecretsManagerAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "secretsmanager:GetSecretValue",
                "secretsmanager:UpdateSecret",
            ],
            resources=[self.secrets_manager_secret_arn],
        ))
        
        # Add CloudWatch Logs permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogs",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=["*"],
        ))
        
        # Add X-Ray permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="XRayTracing",
            effect=iam.Effect.ALLOW,
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
            ],
            resources=["*"],
        ))
        
        # Add VPC permissions if VPC is enabled
        if self.vpc:
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            )
        
        # Add SNS permissions if topic is configured
        if self.sns_topic_arn:
            role.add_to_policy(iam.PolicyStatement(
                sid="SNSPublish",
                effect=iam.Effect.ALLOW,
                actions=["sns:Publish"],
                resources=[self.sns_topic_arn],
            ))
        
        # Create Lambda function
        function = lambda_.Function(
            self, "MediaAuthenticator",
            function_name="media-authenticator",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda_functions/media_authenticator"),
            role=role,
            layers=[self.lambda_layer],
            memory_size=256,
            timeout=Duration.seconds(30),
            environment={
                "SECRET_NAME": "gopro/credentials",
                "TOKEN_EXPIRY_HOURS": "24",
                "SNS_TOPIC_ARN": self.sns_topic_arn or "",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            vpc=self.vpc,
            vpc_subnets=self.vpc_subnets,
            security_groups=[self.security_group] if self.security_group else None,
        )
        
        return function
    
    def _create_media_lister(self) -> lambda_.Function:
        """Create Media Lister Lambda function"""
        
        # Create IAM role
        role = iam.Role(
            self, "MediaListerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Media Lister Lambda function",
        )
        
        # Add Secrets Manager read permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="SecretsManagerReadAccess",
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[self.secrets_manager_secret_arn],
        ))
        
        # Add DynamoDB permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="DynamoDBRead",
            effect=iam.Effect.ALLOW,
            actions=[
                "dynamodb:GetItem",
                "dynamodb:BatchGetItem",
                "dynamodb:Query",
            ],
            resources=[
                self.dynamodb_table_arn,
                f"{self.dynamodb_table_arn}/index/*",
            ],
        ))
        
        # Add CloudWatch Logs permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogs",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=["*"],
        ))
        
        # Add CloudWatch Metrics permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchMetrics",
            effect=iam.Effect.ALLOW,
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "cloudwatch:namespace": "CloudSync/MediaListing"
                }
            },
        ))
        
        # Add X-Ray permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="XRayTracing",
            effect=iam.Effect.ALLOW,
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
            ],
            resources=["*"],
        ))
        
        # Add SNS permissions if topic is configured
        if self.sns_topic_arn:
            role.add_to_policy(iam.PolicyStatement(
                sid="SNSPublish",
                effect=iam.Effect.ALLOW,
                actions=["sns:Publish"],
                resources=[self.sns_topic_arn],
            ))
        
        # Add VPC permissions if VPC is enabled
        if self.vpc:
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            )
        
        # Create Lambda function
        function = lambda_.Function(
            self, "MediaLister",
            function_name="media-lister",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda_functions/media_lister"),
            role=role,
            layers=[self.lambda_layer],
            memory_size=512,
            timeout=Duration.minutes(5),
            environment={
                "SECRET_NAME": "gopro/credentials",
                "DYNAMODB_TABLE": self.dynamodb_table_name,  # Uses actual table name with -dev suffix
                "SNS_TOPIC_ARN": self.sns_topic_arn or "",
                "PAGE_SIZE": "30",  # Match GoPro API page size
                "MAX_VIDEOS": "100",  # Maximum videos per page to download
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            vpc=self.vpc,
            vpc_subnets=self.vpc_subnets,
            security_groups=[self.security_group] if self.security_group else None,
        )
        
        return function
    
    def _create_video_downloader(self) -> lambda_.Function:
        """Create Video Downloader Lambda function"""
        
        # Create IAM role
        role = iam.Role(
            self, "VideoDownloaderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role for Video Downloader Lambda function",
        )
        
        # Add Secrets Manager read permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="SecretsManagerReadAccess",
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[self.secrets_manager_secret_arn],
        ))
        
        # Add S3 permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="S3Upload",
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:PutObject",
                "s3:PutObjectTagging",
                "s3:AbortMultipartUpload",
                "s3:ListMultipartUploadParts",
                "s3:HeadObject",
            ],
            resources=[f"{self.s3_bucket_arn}/gopro-videos/*"],
        ))
        
        # Add KMS permissions for S3 encryption
        role.add_to_policy(iam.PolicyStatement(
            sid="KMSAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "kms:Decrypt",
                "kms:GenerateDataKey",
            ],
            resources=[self.kms_key_arn],
        ))
        
        # Add DynamoDB permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="DynamoDBWrite",
            effect=iam.Effect.ALLOW,
            actions=[
                "dynamodb:UpdateItem",
                "dynamodb:PutItem",
                "dynamodb:GetItem",
            ],
            resources=[self.dynamodb_table_arn],
        ))
        
        # Add CloudWatch Logs permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogs",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=["*"],
        ))
        
        # Add CloudWatch Metrics permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchMetrics",
            effect=iam.Effect.ALLOW,
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "cloudwatch:namespace": "GoProSync"
                }
            },
        ))
        
        # Add X-Ray permissions
        role.add_to_policy(iam.PolicyStatement(
            sid="XRayTracing",
            effect=iam.Effect.ALLOW,
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
            ],
            resources=["*"],
        ))
        
        # Add VPC permissions if VPC is enabled
        if self.vpc:
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            )
        
        # Create Lambda function
        function = lambda_.Function(
            self, "VideoDownloader",
            function_name="video-downloader",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda_functions/video_downloader"),
            role=role,
            layers=[self.lambda_layer],
            memory_size=1024,
            timeout=Duration.minutes(15),
            environment={
                "S3_BUCKET": self.s3_bucket_name,
                "SECRET_NAME": "gopro/credentials",
                "DYNAMODB_TABLE": self.dynamodb_table_name,
                "MULTIPART_THRESHOLD": "104857600",  # 100 MB
                "CHUNK_SIZE": "104857600",  # 100 MB
                "ENVIRONMENT": "dev",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            vpc=self.vpc,
            vpc_subnets=self.vpc_subnets,
            security_groups=[self.security_group] if self.security_group else None,
        )
        
        return function
