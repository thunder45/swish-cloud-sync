#!/usr/bin/env python3
import os
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks, NagSuppressions
from cloud_sync.cloud_sync_stack import CloudSyncStack

app = cdk.App()

# Get environment from context or default to 'dev'
env_name = app.node.try_get_context("environment") or "dev"

# AWS environment configuration
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
)

stack = CloudSyncStack(
    app,
    f"CloudSyncStack-{env_name}",
    env=env,
    environment=env_name,
    description=f"Cloud Sync Application - {env_name} environment"
)

# Add CDK Nag security checks (optional, can be disabled with context)
if app.node.try_get_context("enable_cdk_nag") != "false":
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    
    # Add suppressions for known acceptable violations
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            {
                "id": "AwsSolutions-IAM4",
                "reason": "AWS managed policies are acceptable for Lambda basic execution"
            },
            {
                "id": "AwsSolutions-IAM5",
                "reason": "Wildcard permissions required for X-Ray tracing and CloudWatch metrics"
            }
        ]
    )

app.synth()
