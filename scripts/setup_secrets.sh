#!/bin/bash
# Script to create initial secrets in AWS Secrets Manager

set -e

echo "=== Cloud Sync Application - Secrets Setup ==="
echo ""

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "Error: AWS CLI is not installed"
    echo "Install from: https://aws.amazon.com/cli/"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo "Error: AWS credentials not configured"
    echo "Run: aws configure"
    exit 1
fi

echo "AWS Account: $(aws sts get-caller-identity --query Account --output text)"
echo "AWS Region: $(aws configure get region)"
echo ""

# Prompt for GoPro credentials
read -p "Enter GoPro Client ID: " CLIENT_ID
read -p "Enter GoPro Client Secret: " CLIENT_SECRET
read -p "Enter GoPro Refresh Token: " REFRESH_TOKEN
read -p "Enter GoPro User ID: " USER_ID

# Validate inputs
if [ -z "$CLIENT_ID" ] || [ -z "$CLIENT_SECRET" ] || [ -z "$REFRESH_TOKEN" ] || [ -z "$USER_ID" ]; then
    echo "Error: All fields are required"
    exit 1
fi

# Create secret JSON
SECRET_JSON=$(cat <<EOF
{
  "provider": "gopro",
  "client_id": "$CLIENT_ID",
  "client_secret": "$CLIENT_SECRET",
  "refresh_token": "$REFRESH_TOKEN",
  "access_token": "",
  "user_id": "$USER_ID",
  "token_timestamp": ""
}
EOF
)

echo ""
echo "Creating secret in AWS Secrets Manager..."

# Check if secret already exists
if aws secretsmanager describe-secret --secret-id gopro/credentials &> /dev/null; then
    echo "Secret 'gopro/credentials' already exists"
    read -p "Do you want to update it? (y/n): " UPDATE
    
    if [ "$UPDATE" = "y" ] || [ "$UPDATE" = "Y" ]; then
        aws secretsmanager update-secret \
            --secret-id gopro/credentials \
            --secret-string "$SECRET_JSON"
        echo "✓ Secret updated successfully!"
    else
        echo "Skipping update"
    fi
else
    # Create new secret
    aws secretsmanager create-secret \
        --name gopro/credentials \
        --description "GoPro Cloud API credentials for Cloud Sync Application" \
        --secret-string "$SECRET_JSON"
    echo "✓ Secret created successfully!"
fi

echo ""
echo "=== Testing Authentication ==="

# Test authentication with Python
python3 - <<'PYTHON'
import boto3
import json
import requests
import sys

try:
    # Get credentials from Secrets Manager
    secrets_client = boto3.client('secretsmanager')
    secret = secrets_client.get_secret_value(SecretId='gopro/credentials')
    creds = json.loads(secret['SecretString'])
    
    print("✓ Retrieved credentials from Secrets Manager")
    
    # Test token refresh
    response = requests.post(
        "https://api.gopro.com/v1/oauth2/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": creds['refresh_token'],
            "client_id": creds['client_id'],
            "client_secret": creds['client_secret']
        }
    )
    
    if response.status_code == 200:
        tokens = response.json()
        print("✓ Authentication successful!")
        print(f"  Access token expires in: {tokens['expires_in']} seconds")
        
        # Update secret with new access token
        creds['access_token'] = tokens['access_token']
        creds['token_timestamp'] = tokens.get('issued_at', '')
        
        secrets_client.update_secret(
            SecretId='gopro/credentials',
            SecretString=json.dumps(creds)
        )
        print("✓ Updated secret with new access token")
    else:
        print(f"✗ Authentication failed: {response.status_code}")
        print(f"  Response: {response.text}")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ Error: {str(e)}")
    sys.exit(1)
PYTHON

if [ $? -eq 0 ]; then
    echo ""
    echo "=== Setup Complete ==="
    echo "Secret 'gopro/credentials' is ready to use"
    echo "You can now deploy the Cloud Sync Application"
else
    echo ""
    echo "=== Setup Failed ==="
    echo "Please check your credentials and try again"
    exit 1
fi
