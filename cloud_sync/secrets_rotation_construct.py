"""
Secrets Rotation Construct

Creates Lambda function and EventBridge rule for automatic secrets rotation.
"""

from aws_cdk import (
    Duration,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_logs as logs,
    aws_sqs as sqs,
)
from constructs import Construct


class SecretsRotationConstruct(Construct):
    """Construct for secrets rotation infrastructure."""
    
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        secret_name: str,
        provider_name: str,
        sns_topic_arn: str,
        lambda_layer: lambda_.LayerVersion,
        vpc=None,
        security_group=None,
        **kwargs
    ) -> None:
        """
        Initialize Secrets Rotation Construct.
        
        Args:
            scope: CDK scope
            construct_id: Construct ID
            secret_name: Name of the secret to rotate
            provider_name: Name of the cloud provider
            sns_topic_arn: ARN of SNS topic for notifications
            lambda_layer: Lambda layer with shared code
            vpc: Optional VPC for Lambda
            security_group: Optional security group for Lambda
        """
        super().__init__(scope, construct_id, **kwargs)
        
        # Create Dead Letter Queue for rotation failures
        self.dlq = sqs.Queue(
            self,
            'RotatorDLQ',
            queue_name=f'{provider_name}-secrets-rotator-dlq',
            retention_period=Duration.days(14),
        )
        
        # Create Lambda function for secrets rotation
        self.rotator_function = lambda_.Function(
            self,
            'SecretsRotatorFunction',
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler='handler.handler',
            code=lambda_.Code.from_asset('lambda_functions/secrets_rotator'),
            timeout=Duration.seconds(60),
            memory_size=256,
            layers=[lambda_layer],
            environment={
                'SECRET_NAME': secret_name,
                'SNS_TOPIC_ARN': sns_topic_arn,
                'PROVIDER_NAME': provider_name,
                'GOPRO_CLIENT_ID': '',  # Set via environment or Parameter Store
                'GOPRO_CLIENT_SECRET': '',  # Set via environment or Parameter Store
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            description=f'Rotates {provider_name} credentials in Secrets Manager',
            vpc=vpc,
            vpc_subnets={'subnet_type': 'PRIVATE_WITH_EGRESS'} if vpc else None,
            security_groups=[security_group] if security_group else None,
            dead_letter_queue=self.dlq,
            dead_letter_queue_enabled=True,
        )
        
        # Grant permissions to read/write secrets
        self.rotator_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    'secretsmanager:GetSecretValue',
                    'secretsmanager:UpdateSecret',
                    'secretsmanager:DescribeSecret',
                ],
                resources=[f'arn:aws:secretsmanager:*:*:secret:{secret_name}*'],
            )
        )
        
        # Grant permissions to publish to SNS
        self.rotator_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=['sns:Publish'],
                resources=[sns_topic_arn],
            )
        )
        
        # Grant permissions to publish CloudWatch metrics
        self.rotator_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=['cloudwatch:PutMetricData'],
                resources=['*'],
                conditions={
                    'StringEquals': {
                        'cloudwatch:namespace': 'CloudSync/SecretsRotation'
                    }
                }
            )
        )
        
        # Create EventBridge rule for 30-day rotation schedule
        # Run at 2 AM UTC on the 1st of every month
        # Note: Using UTC to avoid DST complications. Adjust hour value if specific
        # local time is required (e.g., hour='1' for 2 AM CET during summer DST)
        rotation_rule = events.Rule(
            self,
            'RotationScheduleRule',
            schedule=events.Schedule.cron(
                minute='0',
                hour='2',  # 2 AM UTC
                day='1',
                month='*',
                year='*'
            ),
            description=f'Triggers {provider_name} secrets rotation monthly at 2 AM UTC',
        )
        
        # Add Lambda as target
        rotation_rule.add_target(
            targets.LambdaFunction(
                self.rotator_function,
                retry_attempts=2,
            )
        )
        
        # Store references
        self.secret_name = secret_name
        self.provider_name = provider_name
