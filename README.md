# Cloud Sync Application

Automated, serverless system for synchronizing video content from cloud storage providers (starting with GoPro Cloud) to AWS S3 cost-optimized storage tiers.

## Overview

The Cloud Sync Application is a fully automated solution that:
- Discovers new videos from cloud providers daily
- Transfers videos to AWS S3 with intelligent lifecycle management
- Achieves 95% cost reduction through automatic storage tier transitions
- Provides comprehensive monitoring and alerting
- Handles errors gracefully with automatic retry logic

## Architecture

Built on AWS serverless technologies:
- **EventBridge**: Scheduled daily sync execution
- **Step Functions**: Workflow orchestration with error handling
- **Lambda**: Serverless compute for sync operations
- **S3**: Cost-optimized storage with lifecycle policies
- **DynamoDB**: State tracking to prevent duplicates
- **Secrets Manager**: Secure credential management
- **CloudWatch**: Logging, metrics, and alerting

## Project Structure

```
.
├── app.py                          # CDK app entry point
├── cdk.json                        # CDK configuration
├── requirements.txt                # Python dependencies
├── cloud_sync/                     # CDK stack definitions
│   ├── __init__.py
│   ├── cloud_sync_stack.py        # Main stack
│   └── config.py                   # Environment configuration
├── lambda_layer/                   # Shared Lambda layer
│   ├── requirements.txt
│   └── python/
│       └── cloud_sync_common/     # Common utilities
│           ├── __init__.py
│           ├── provider_interface.py  # Provider abstraction
│           ├── exceptions.py          # Custom exceptions
│           ├── retry_utils.py         # Retry logic
│           ├── logging_utils.py       # Structured logging
│           ├── metrics_utils.py       # CloudWatch metrics
│           ├── correlation.py         # Request tracing
│           └── xray_utils.py          # X-Ray tracing
└── lambdas/                        # Lambda function code
    ├── media_authenticator/
    ├── media_lister/
    └── video_downloader/
```

## Prerequisites

- Python 3.12+
- AWS CLI configured with appropriate credentials
- AWS CDK CLI installed (`npm install -g aws-cdk`)
- GoPro Developer account with OAuth credentials

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd cloud-sync-application
```

2. Create virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Install Lambda layer dependencies:
```bash
cd lambda_layer
pip install -r requirements.txt -t python/
cd ..
```

## Configuration

### Environment Selection

The application supports three environments: `dev`, `staging`, and `prod`. Configure via CDK context:

```bash
# Deploy to dev (default)
cdk deploy

# Deploy to staging
cdk deploy -c environment=staging

# Deploy to prod
cdk deploy -c environment=prod
```

### GoPro OAuth Setup

1. Register application at [GoPro Developer Portal](https://developers.gopro.com)
2. Obtain `client_id` and `client_secret`
3. Perform initial OAuth flow to get `refresh_token`
4. Store credentials in AWS Secrets Manager (see deployment steps)

## Deployment

### First-time Deployment

1. Bootstrap CDK (one-time per account/region):
```bash
cdk bootstrap
```

2. Synthesize CloudFormation template:
```bash
cdk synth
```

3. Deploy infrastructure:
```bash
cdk deploy
```

4. Store GoPro credentials in Secrets Manager:
```bash
aws secretsmanager create-secret \
  --name gopro/credentials \
  --secret-string '{
    "provider": "gopro",
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "refresh_token": "YOUR_REFRESH_TOKEN",
    "user_id": "YOUR_USER_ID"
  }'
```

### Subsequent Deployments

```bash
cdk deploy
```

## Usage

### Manual Execution

Trigger sync manually via AWS Console or CLI:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT:stateMachine:gopro-sync-orchestrator \
  --input '{"provider": "gopro"}'
```

### Scheduled Execution

The system automatically runs daily at 2:00 AM CET via EventBridge scheduler.

### Monitoring

1. **CloudWatch Dashboard**: View real-time metrics
   - Navigate to CloudWatch > Dashboards > GoPro-Sync-Operations

2. **CloudWatch Logs**: View execution logs
   - Log groups: `/aws/lambda/media-*`, `/aws/states/gopro-sync-orchestrator`

3. **X-Ray Service Map**: Visualize request flow
   - Navigate to X-Ray > Service map

4. **Alarms**: Receive alerts via SNS
   - Subscribe to topic: `gopro-sync-alerts`

## Development

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-cov moto

# Run unit tests
pytest tests/unit

# Run integration tests
pytest tests/integration

# Run with coverage
pytest --cov=cloud_sync --cov=lambda_layer tests/
```

### Local Development

Test Lambda functions locally using AWS SAM:

```bash
sam local invoke MediaAuthenticator -e events/auth_event.json
```

### Code Quality

```bash
# Format code
black cloud_sync/ lambda_layer/ lambdas/

# Lint code
pylint cloud_sync/ lambda_layer/ lambdas/

# Type checking
mypy cloud_sync/ lambda_layer/ lambdas/
```

## Cost Estimation

### Monthly Costs (100 GB transfer)

**Initial Month:**
- Lambda: $2.50
- Step Functions: $0.10
- DynamoDB: $0.50
- S3 (transition period): $0.68
- **Total: ~$3.78**

**Ongoing Monthly:**
- Lambda: $2.50
- Step Functions: $0.10
- DynamoDB: $0.50
- S3 Deep Archive: $0.10
- **Total: ~$3.20**

**With VPC (Production):**
- Add NAT Gateway: ~$32/month
- Add VPC Endpoints: ~$21/month (3 endpoints)
- **Total: ~$56/month**

## Troubleshooting

### Authentication Failures

1. Check Secrets Manager for valid credentials
2. Verify OAuth refresh token hasn't expired
3. Check CloudWatch Logs for detailed error messages

### Transfer Failures

1. Check CloudWatch Logs for specific error
2. Verify source video still exists in GoPro Cloud
3. Check DynamoDB for retry count
4. Review Dead Letter Queue for persistent failures

### High Costs

1. Review S3 lifecycle policy configuration
2. Check for failed transfers causing retries
3. Verify Lambda memory allocation is appropriate
4. Consider disabling VPC in non-production environments

## Security

- All data encrypted in transit (TLS 1.2+) and at rest (KMS)
- IAM roles follow least privilege principle
- Secrets stored in AWS Secrets Manager with automatic rotation
- S3 bucket blocks all public access
- CloudTrail enabled for audit logging

## Contributing

1. Create feature branch from `main`
2. Make changes with tests
3. Run code quality checks
4. Submit pull request with description

## License

[Your License Here]

## Support

For issues and questions:
- Create GitHub issue
- Contact: [Your Contact Info]

## Roadmap

- [ ] Support for additional cloud providers (Google Drive, Dropbox)
- [ ] Web UI for monitoring and configuration
- [ ] Advanced filtering (date ranges, file types)
- [ ] Parallel multi-provider sync
- [ ] Cost optimization recommendations
