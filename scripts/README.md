# Scripts Directory

This directory contains utility scripts for managing the GoPro Cloud Sync application.

## Available Scripts

1. **update_gopro_tokens.sh** - Update cookies in AWS Secrets Manager
2. **list_gopro_videos.py** - List your GoPro camera content
3. **debug_gopro_api.py** - Debug GoPro API responses
4. **trigger_sync.sh** - Manually trigger sync execution
5. **run_tests.sh** - Run unit tests with coverage

---

## Token Management

### update_gopro_tokens.sh

Updates GoPro authentication cookies in AWS Secrets Manager with validation.

**Prerequisites:**
- AWS CLI installed and configured
- jq (JSON processor) installed
- Valid AWS credentials with Secrets Manager permissions

**Usage:**

1. Copy the entire Cookie header from your browser (see `docs/TOKEN_EXTRACTION_GUIDE.md`)

2. Create a file with your cookies:
   ```bash
   cp scripts/cookies.txt.template scripts/cookies.txt
   ```

3. Edit `scripts/cookies.txt` and paste your entire cookie string (replace the template content)

4. Run the script:
   ```bash
   ./scripts/update_gopro_tokens.sh
   ```

5. When prompted, enter: `scripts/cookies.txt`

**Note:** `scripts/cookies.txt` is gitignored to prevent accidental commits.

The script will automatically:
- Extract the `gp_access_token` from your cookies
- Store both the token and full cookie string in AWS Secrets Manager
- Validate the tokens with a test API call

**Features:**
- ✅ Token validation with test API call
- ✅ Automatic secret creation/update in AWS Secrets Manager
- ✅ User-friendly prompts and error messages
- ✅ Success/failure reporting
- ✅ Final validation of stored tokens

**Troubleshooting:**

If tokens fail validation:
- Ensure you extracted fresh tokens from your browser
- Check that cookies include all required values
- Verify the gp-access-token is a valid JWT
- Refer to `docs/TOKEN_EXTRACTION_GUIDE.md` for extraction instructions

---

## Media Management

### list_gopro_videos.py

Lists all GoPro camera content (videos + photos) from your GoPro Cloud account.

**Prerequisites:**
- AWS CLI configured
- Python 3.12+
- `gopro/credentials` secret exists in Secrets Manager

**Usage:**

```bash
python3 scripts/list_gopro_videos.py
```

**Output:**
- Table of media (ID, filename, size, duration)
- Summary statistics (total count, size, duration)
- Cookie age verification

**What it shows:**
- Only GoPro camera files (GH*.*, GO*.*)
- Excludes Pixel phone uploads (PXL_*)
- Excludes items with no filename

**Useful for:**
- Verifying cookies are working
- Seeing what will be synced
- Checking library contents

---

### debug_gopro_api.py

Debug tool for testing GoPro API endpoints and inspecting responses.

**Prerequisites:**
- AWS CLI configured
- Python 3.12+
- `gopro/credentials` secret exists

**Usage:**

```bash
python3 scripts/debug_gopro_api.py
```

**What it tests:**
- `/media/search` with various parameters
- Different endpoints and pagination
- Response structure analysis
- API availability

**Output:**
- HTTP status codes
- Response headers
- Full JSON responses (pretty-printed)
- Structure analysis (keys, counts, types)

**Useful for:**
- Diagnosing API issues
- Detecting API structure changes
- Understanding response formats
- Troubleshooting authentication

---

## Sync Trigger

### trigger_sync.sh

Manually triggers a sync operation via AWS Step Functions.

**Usage:**

```bash
./scripts/trigger_sync.sh
```

**Note:** Requires Step Functions state machine to be deployed (Task 8).

---

## Testing

### run_tests.sh

Runs all unit tests with coverage reporting.

**Prerequisites:**
- Development dependencies installed (`pip install -r requirements-dev.txt`)

**Usage:**

```bash
./scripts/run_tests.sh
```

**What it does:**
- Runs all unit tests in `tests/unit/`
- Generates coverage report
- Opens HTML coverage report in browser

**Output:**
- Test results (pass/fail)
- Coverage percentage
- HTML report in `htmlcov/`

---

## Template Files

### cookies.txt.template

Template for storing cookie strings temporarily during token extraction.

**Usage:**

1. Copy template:
   ```bash
   cp scripts/cookies.txt.template scripts/cookies.txt
   ```

2. Edit `scripts/cookies.txt` and paste your entire cookie string

3. Run update script:
   ```bash
   ./scripts/update_gopro_tokens.sh
   ```

**Security Note:** `scripts/cookies.txt` is gitignored. Delete after use to avoid leaving sensitive data on disk.

---

## Common Workflows

### First-Time Setup

```bash
# 1. Extract cookies (see docs/TOKEN_EXTRACTION_GUIDE.md)
# 2. Create cookies file
cp scripts/cookies.txt.template scripts/cookies.txt
# 3. Edit cookies.txt with your extracted cookies
# 4. Update Secrets Manager
./scripts/update_gopro_tokens.sh
# 5. Verify it works
python3 scripts/list_gopro_videos.py
```

### Refresh Expired Cookies

```bash
# 1. Extract fresh cookies from browser
# 2. Update cookies.txt with new values
# 3. Update Secrets Manager
./scripts/update_gopro_tokens.sh
# 4. Verify
python3 scripts/list_gopro_videos.py
# 5. Clean up
rm scripts/cookies.txt
```

### Debug API Issues

```bash
# Run debug tool
python3 scripts/debug_gopro_api.py

# Check specific endpoint responses
# Analyze structure differences
# Compare with expected format
```

### Run Tests Before Deployment

```bash
# Install dev dependencies (one-time)
pip install -r requirements-dev.txt

# Run tests
./scripts/run_tests.sh

# Check coverage report
open htmlcov/index.html
```

---

## Script Permissions

Make scripts executable:

```bash
chmod +x scripts/*.sh
chmod +x scripts/*.py
```

---

## Troubleshooting

### "AWS CLI not configured"
```bash
aws configure
# Enter your credentials
```

### "jq not installed"
```bash
# macOS
brew install jq

# Linux
sudo apt-get install jq  # Debian/Ubuntu
sudo yum install jq      # RedHat/CentOS
```

### "Secret not found"
```bash
# Create it first
./scripts/update_gopro_tokens.sh
```

### "Failed to list videos"
```bash
# Cookies may have expired
# Extract fresh cookies and update
./scripts/update_gopro_tokens.sh
```

---

## Security Best Practices

1. **Never commit `scripts/cookies.txt`** - It's gitignored for safety
2. **Delete cookies.txt after use** - Don't leave sensitive data on disk
3. **Rotate cookies regularly** - Extract fresh cookies periodically
4. **Use AWS IAM roles** - Don't hardcode AWS credentials
5. **Review CloudTrail logs** - Monitor secret access

---

**Last Updated**: December 1, 2025  
**Scripts**: 5 utilities available  
**Status**: All scripts tested and working
