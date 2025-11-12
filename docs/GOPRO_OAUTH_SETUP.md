# GoPro OAuth 2.0 Setup Guide

This guide walks through setting up OAuth 2.0 authentication with GoPro Cloud API.

## Prerequisites

- GoPro account with videos uploaded to GoPro Cloud
- Access to GoPro Developer Portal

## Step 1: Register Application

1. Navigate to [GoPro Developer Portal](https://developers.gopro.com)
2. Sign in with your GoPro account
3. Click "Create New Application"
4. Fill in application details:
   - **Application Name**: Cloud Sync Application
   - **Description**: Automated video synchronization to AWS S3
   - **Redirect URI**: `http://localhost:8080/callback` (for initial setup)
   - **Scopes**: Select all media-related scopes (read, download)

5. Submit the application for review
6. Once approved, note down:
   - **Client ID**: `YOUR_CLIENT_ID`
   - **Client Secret**: `YOUR_CLIENT_SECRET`

## Step 2: Obtain Initial Refresh Token

### Option A: Using GoPro's OAuth Flow (Recommended)

1. Create a simple Python script to handle OAuth flow:

```python
#!/usr/bin/env python3
"""Script to obtain GoPro OAuth refresh token."""

import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import webbrowser

# Replace with your credentials
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REDIRECT_URI = "http://localhost:8080/callback"

# Authorization URL
AUTH_URL = (
    f"https://api.gopro.com/v1/oauth2/authorize?"
    f"client_id={CLIENT_ID}&"
    f"redirect_uri={REDIRECT_URI}&"
    f"response_type=code&"
    f"scope=media:read media:download"
)

class CallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback."""
    
    def do_GET(self):
        """Handle GET request with authorization code."""
        query = urlparse(self.path).query
        params = parse_qs(query)
        
        if 'code' in params:
            auth_code = params['code'][0]
            
            # Exchange code for tokens
            token_response = requests.post(
                "https://api.gopro.com/v1/oauth2/token",
                json={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "redirect_uri": REDIRECT_URI
                }
            )
            
            if token_response.status_code == 200:
                tokens = token_response.json()
                
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                
                html = f"""
                <html>
                <body>
                    <h1>Success!</h1>
                    <p>Save these credentials:</p>
                    <pre>
Access Token: {tokens['access_token']}
Refresh Token: {tokens['refresh_token']}
Expires In: {tokens['expires_in']} seconds
User ID: {tokens.get('user_id', 'N/A')}
                    </pre>
                    <p>You can close this window.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode())
                
                print("\n=== OAuth Tokens ===")
                print(f"Access Token: {tokens['access_token']}")
                print(f"Refresh Token: {tokens['refresh_token']}")
                print(f"Expires In: {tokens['expires_in']} seconds")
                print(f"User ID: {tokens.get('user_id', 'N/A')}")
                print("\nSave the refresh token for AWS Secrets Manager!")
            else:
                print(f"Token exchange failed: {token_response.text}")
        
        else:
            self.send_response(400)
            self.end_headers()

if __name__ == "__main__":
    print("Starting OAuth flow...")
    print(f"Opening browser to: {AUTH_URL}")
    webbrowser.open(AUTH_URL)
    
    print("\nWaiting for callback on http://localhost:8080...")
    server = HTTPServer(('localhost', 8080), CallbackHandler)
    server.handle_request()
    print("\nOAuth flow complete!")
```

2. Run the script:
```bash
python3 gopro_oauth.py
```

3. Browser will open to GoPro authorization page
4. Log in and authorize the application
5. Script will display your tokens
6. **Save the refresh token** - you'll need it for AWS Secrets Manager

### Option B: Using Manual Token Extraction

If you have an existing GoPro session:

1. Log into GoPro Cloud web interface
2. Open browser developer tools (F12)
3. Go to Network tab
4. Refresh the page
5. Look for API calls to `api.gopro.com`
6. Find the `Authorization` header
7. Extract the JWT token (starts with `eyJ...`)

**Note**: Manual tokens expire after 24 hours. Use Option A for long-term refresh tokens.

## Step 3: Store Credentials in AWS Secrets Manager

### Using AWS CLI

```bash
aws secretsmanager create-secret \
  --name gopro/credentials \
  --description "GoPro Cloud API credentials for Cloud Sync Application" \
  --secret-string '{
    "provider": "gopro",
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "refresh_token": "YOUR_REFRESH_TOKEN",
    "access_token": "",
    "user_id": "YOUR_USER_ID",
    "token_timestamp": ""
  }'
```

### Using AWS Console

1. Navigate to AWS Secrets Manager
2. Click "Store a new secret"
3. Select "Other type of secret"
4. Add key-value pairs:
   - `provider`: `gopro`
   - `client_id`: Your client ID
   - `client_secret`: Your client secret
   - `refresh_token`: Your refresh token
   - `access_token`: (leave empty)
   - `user_id`: Your GoPro user ID
   - `token_timestamp`: (leave empty)
5. Secret name: `gopro/credentials`
6. Click "Store"

## Step 4: Configure Environment Variables

Add to your CDK context or environment:

```bash
export GOPRO_CLIENT_ID="YOUR_CLIENT_ID"
export GOPRO_CLIENT_SECRET="YOUR_CLIENT_SECRET"
```

Or add to AWS Systems Manager Parameter Store:

```bash
aws ssm put-parameter \
  --name "/cloudsync/gopro/client_id" \
  --value "YOUR_CLIENT_ID" \
  --type "String"

aws ssm put-parameter \
  --name "/cloudsync/gopro/client_secret" \
  --value "YOUR_CLIENT_SECRET" \
  --type "SecureString"
```

## Step 5: Test Authentication

Test the authentication flow:

```python
import boto3
import json
import requests

# Get credentials from Secrets Manager
secrets_client = boto3.client('secretsmanager')
secret = secrets_client.get_secret_value(SecretId='gopro/credentials')
creds = json.loads(secret['SecretString'])

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
    print("✓ Authentication successful!")
    tokens = response.json()
    print(f"Access token expires in: {tokens['expires_in']} seconds")
else:
    print(f"✗ Authentication failed: {response.text}")
```

## Troubleshooting

### "Invalid client credentials"
- Verify client_id and client_secret are correct
- Check if application is approved in GoPro Developer Portal

### "Invalid refresh token"
- Refresh tokens may expire after extended periods
- Re-run OAuth flow to obtain new refresh token

### "Insufficient scopes"
- Ensure application has `media:read` and `media:download` scopes
- Re-authorize application with correct scopes

## Security Best Practices

1. **Never commit credentials to version control**
   - Use `.gitignore` to exclude secrets files
   - Use environment variables or AWS Secrets Manager

2. **Rotate credentials regularly**
   - Set up automatic rotation in Secrets Manager
   - Monitor for unauthorized access

3. **Use least privilege**
   - Only request necessary OAuth scopes
   - Limit IAM permissions to required actions

4. **Monitor usage**
   - Enable CloudTrail for Secrets Manager access
   - Set up alerts for unusual access patterns

## References

- [GoPro Developer Portal](https://developers.gopro.com)
- [GoPro API Documentation](https://developers.gopro.com/docs)
- [OAuth 2.0 Specification](https://oauth.net/2/)
- [AWS Secrets Manager Documentation](https://docs.aws.amazon.com/secretsmanager/)
