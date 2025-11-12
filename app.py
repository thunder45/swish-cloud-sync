#!/usr/bin/env python3
import os
import aws_cdk as cdk
from cloud_sync.cloud_sync_stack import CloudSyncStack

app = cdk.App()

# Get environment from context or default to 'dev'
env_name = app.node.try_get_context("environment") or "dev"

# AWS environment configuration
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
)

CloudSyncStack(
    app,
    f"CloudSyncStack-{env_name}",
    env=env,
    environment=env_name,
    description=f"Cloud Sync Application - {env_name} environment"
)

app.synth()
