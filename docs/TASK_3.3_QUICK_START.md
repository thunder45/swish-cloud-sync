# Task 3.3 Quick Start: Create Initial Secrets

**Status:** 🔴 **CRITICAL - MUST COMPLETE FIRST**  
This task is a blocker for all Lambda functions and testing.

## What You'll Do

Extract authentication cookies from your browser and store them in AWS Secrets Manager so the Lambda functions can access your GoPro Cloud account.

## Time Required

⏱️ **10-15 minutes** (first time)  
⏱️ **5 minutes** (subsequent updates)

## Prerequisites

✅ GoPro Cloud account with videos  
✅ Chrome or Firefox browser  
✅ AWS CLI configured (`aws sts get-caller-identity` works)  
✅ `jq` installed (`brew install jq` on macOS)

## Step-by-Step Process

### Step 1: Extract Cookies from Browser

Follow the detailed guide: **[docs/TOKEN_EXTRACTION_GUIDE.md](TOKEN_EXTRACTION_GUIDE.md)**

**Quick Summary:**
1. Open Chrome/Firefox
2. Go to https://gopro.com/media-library/ and login
3. Press F12 to open Developer Tools
4. Click "Network" tab
5. Check "Preserve log"
6. Scroll in media library to trigger API requests
7. Filter by "api.gopro.com"
8. Click on any request to api.gopro.com
9. Click "Headers" tab
10. Find the "Cookie" header in Request Headers
11. Copy the ENTIRE cookie string

**What you're looking for:**
```
Cookie: gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ...; gp_user_id=7cb49f28-...; session=...; ...
```

### Step 2: Save Cookie String to File

```bash
cd /Volumes/workplace/swish-cloud-sync

# Copy the template
cp scripts/cookies.txt.template scripts/cookies.txt

# Open in your text editor
# Replace ALL content with your copied cookie string
# Save and close
```

**Important:** Paste the ENTIRE cookie string - don't try to parse it yourself. The script will extract what it needs.

### Step 3: Run Update Script

```bash
# Make script executable (if not already)
chmod +x scripts/update_gopro_tokens.sh

# Run the script
./scripts/update_gopro_tokens.sh
```

**When prompted:**
```
Enter path to file containing raw cookie string:
scripts/cookies.txt    ← Type this and press Enter
```

### Step 4: Verify Success

The script will:
1. ✅ Extract `gp_access_token` from your cookies
2. ✅ Validate tokens with GoPro API (test call)
3. ✅ Create/update secret in AWS Secrets Manager (`gopro/credentials`)
4. ✅ Perform final validation with stored tokens

**Success looks like:**
```
✓ Token validation successful (HTTP 200)
✓ Secret updated successfully
✓ Final validation successful
✓ Stored tokens are working correctly
```

## What Gets Stored in Secrets Manager

```json
{
  "gp-access-token": "eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ...",
  "cookies": "gp_access_token=...; gp_user_id=...; session=...; ...",
  "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
  "last_updated": "2025-12-01T10:00:00Z"
}
```

**Secret Name:** `gopro/credentials`  
**Encryption:** AWS managed key (automatic)  
**Access:** Lambda execution roles (configured in CDK)

## Verification

After successful update, verify the secret exists:

```bash
# Check secret exists
aws secretsmanager describe-secret --secret-id gopro/credentials

# View secret value (be careful - this shows sensitive data)
aws secretsmanager get-secret-value --secret-id gopro/credentials --query SecretString --output text | jq '.'
```

## Troubleshooting

### ❌ "gp_access_token not found in cookies"

**Problem:** The cookie string doesn't contain the required token.

**Solution:**
1. Make sure you copied from an api.gopro.com request (not gopro.com)
2. Verify you're logged into GoPro Media Library
3. The request should show Status 200 (green)
4. Try a different api.gopro.com request

### ❌ "Token validation failed (HTTP 401/403)"

**Problem:** Tokens are expired or invalid.

**Solution:**
1. Log out of GoPro Cloud completely
2. Clear browser cookies
3. Log back in
4. Extract fresh tokens immediately

### ❌ "AWS credentials not configured"

**Problem:** AWS CLI is not configured.

**Solution:**
```bash
aws configure
# Enter your AWS Access Key ID
# Enter your AWS Secret Access Key
# Enter default region (e.g., us-east-1)
# Enter default output format (json)
```

### ❌ "jq is not installed"

**Problem:** Missing jq utility.

**Solution:**
```bash
# macOS
brew install jq

# Linux
sudo apt-get install jq  # Debian/Ubuntu
sudo yum install jq      # RedHat/CentOS
```

## Token Lifespan & Maintenance

⏰ **Token Lifespan:** Typically 1-4 weeks (exact duration unknown)  
🔄 **Refresh Frequency:** Manual refresh required when tokens expire  
📧 **Alerts:** SNS notifications will alert you when tokens expire

**When tokens expire:**
1. You'll receive an SNS email alert
2. CloudWatch logs will show 401/403 errors
3. Simply repeat this process to refresh tokens

## Security Best Practices

🔒 **After extracting tokens:**
1. Delete `scripts/cookies.txt` (contains sensitive data)
2. Clear your clipboard
3. Consider logging out of GoPro Cloud

🔒 **Token storage:**
- Tokens are encrypted at rest in Secrets Manager
- Only Lambda functions with proper IAM roles can access them
- AWS CloudTrail logs all access attempts

## Next Steps

Once Task 3.3 is complete:

✅ **You can now:**
- Implement Token Validator Lambda (Task 5)
- Implement Media Lister Lambda (Task 6)
- Test the entire sync workflow
- Deploy to dev environment

❌ **You cannot proceed without this:**
All Lambda functions need these credentials to authenticate with GoPro Cloud.

## Quick Reference

```bash
# Extract cookies → Save to scripts/cookies.txt → Run:
./scripts/update_gopro_tokens.sh

# Verify secret:
aws secretsmanager describe-secret --secret-id gopro/credentials

# Delete sensitive file:
rm scripts/cookies.txt
```

## Support

📖 **Detailed extraction guide:** [docs/TOKEN_EXTRACTION_GUIDE.md](TOKEN_EXTRACTION_GUIDE.md)  
📖 **Cookie testing strategy:** [docs/COOKIE_TESTING_STRATEGY.md](COOKIE_TESTING_STRATEGY.md)  
📖 **Update script:** [scripts/update_gopro_tokens.sh](../scripts/update_gopro_tokens.sh)

---

**Ready?** Follow the steps above to complete Task 3.3, then we can move on to implementing the Lambda functions! 🚀
