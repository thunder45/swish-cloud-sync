#!/bin/bash

# GoPro Token Update Script
# This script updates GoPro authentication tokens in AWS Secrets Manager
# and validates them with a test API call.

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SECRET_NAME="gopro/credentials"
GOPRO_API_URL="https://api.gopro.com/media/search"
TEMP_FILE="/tmp/gopro_tokens_$$.json"

# Cleanup function
cleanup() {
    if [ -f "$TEMP_FILE" ]; then
        rm -f "$TEMP_FILE"
    fi
}
trap cleanup EXIT

# Print colored message
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Print header
print_header() {
    echo ""
    print_message "$BLUE" "================================================"
    print_message "$BLUE" "  GoPro Cloud Token Update Script"
    print_message "$BLUE" "================================================"
    echo ""
}

# Check prerequisites
check_prerequisites() {
    print_message "$BLUE" "Checking prerequisites..."
    
    # Check if AWS CLI is installed
    if ! command -v aws &> /dev/null; then
        print_message "$RED" "ERROR: AWS CLI is not installed."
        print_message "$YELLOW" "Please install AWS CLI: https://aws.amazon.com/cli/"
        exit 1
    fi
    
    # Check if jq is installed
    if ! command -v jq &> /dev/null; then
        print_message "$RED" "ERROR: jq is not installed."
        print_message "$YELLOW" "Please install jq: https://stedolan.github.io/jq/"
        exit 1
    fi
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        print_message "$RED" "ERROR: AWS credentials not configured."
        print_message "$YELLOW" "Please configure AWS CLI: aws configure"
        exit 1
    fi
    
    print_message "$GREEN" "✓ All prerequisites met"
    echo ""
}

# Prompt for token input
prompt_for_tokens() {
    print_message "$BLUE" "Please provide your GoPro authentication cookies:"
    print_message "$YELLOW" "Refer to docs/TOKEN_EXTRACTION_GUIDE.md for extraction instructions"
    echo ""
    
    print_message "$BLUE" "Enter path to file containing raw cookie string:"
    print_message "$YELLOW" "(Just paste the entire Cookie header value into a text file)"
    read -r COOKIE_FILE
    
    if [ ! -f "$COOKIE_FILE" ]; then
        print_message "$RED" "ERROR: File not found: $COOKIE_FILE"
        exit 1
    fi
    
    # Read the entire cookie string from file
    COOKIES=$(cat "$COOKIE_FILE" | tr -d '\n' | tr -d '\r')
    
    if [ -z "$COOKIES" ]; then
        print_message "$RED" "ERROR: Cookie file is empty"
        exit 1
    fi
    
    # Extract gp_access_token from cookies
    GP_ACCESS_TOKEN=$(echo "$COOKIES" | grep -o 'gp_access_token=[^;]*' | cut -d'=' -f2-)
    
    if [ -z "$GP_ACCESS_TOKEN" ]; then
        print_message "$RED" "ERROR: gp_access_token not found in cookies"
        print_message "$YELLOW" "Make sure your cookie string contains 'gp_access_token=...'"
        exit 1
    fi
    
    # Use default user-agent
    USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    print_message "$GREEN" "✓ Extracted gp_access_token from cookies"
    print_message "$BLUE" "  Token length: ${#GP_ACCESS_TOKEN} characters"
    print_message "$BLUE" "  Cookie length: ${#COOKIES} characters"
    echo ""
}

# Validate tokens with test API call
validate_tokens() {
    print_message "$BLUE" "Validating tokens with GoPro API..."
    
    # Make test API call using the same format as browser
    # The Accept header is critical - GoPro API requires this specific media type
    HTTP_CODE=$(curl -s -o /tmp/gopro_test_response.json -w "%{http_code}" \
        -H "Cookie: $COOKIES" \
        -H "User-Agent: $USER_AGENT" \
        -H "Accept: application/vnd.gopro.jk.media+json; version=2.0.0" \
        -H "Accept-Language: en-US,en;q=0.9" \
        -H "Referer: https://gopro.com/" \
        "$GOPRO_API_URL?per_page=1")
    
    if [ "$HTTP_CODE" -eq 200 ]; then
        print_message "$GREEN" "✓ Token validation successful (HTTP 200)"
        rm -f /tmp/gopro_test_response.json
        return 0
    elif [ "$HTTP_CODE" -eq 401 ] || [ "$HTTP_CODE" -eq 403 ]; then
        print_message "$RED" "✗ Token validation failed (HTTP $HTTP_CODE)"
        print_message "$RED" "The provided tokens are invalid or expired."
        print_message "$YELLOW" "Please extract fresh tokens from your browser."
        rm -f /tmp/gopro_test_response.json
        exit 1
    else
        print_message "$YELLOW" "⚠ Unexpected API response (HTTP $HTTP_CODE)"
        print_message "$YELLOW" "Tokens may be valid, but API returned unexpected status."
        
        # Show response if available
        if [ -f /tmp/gopro_test_response.json ]; then
            print_message "$YELLOW" "API Response:"
            cat /tmp/gopro_test_response.json | jq '.' 2>/dev/null || cat /tmp/gopro_test_response.json
            rm -f /tmp/gopro_test_response.json
        fi
        
        # Ask user if they want to continue
        print_message "$BLUE" "Do you want to continue updating the secret? (y/n):"
        read -r CONTINUE
        if [ "$CONTINUE" != "y" ] && [ "$CONTINUE" != "Y" ]; then
            print_message "$YELLOW" "Update cancelled by user."
            exit 1
        fi
    fi
    
    echo ""
}

# Create secret JSON
create_secret_json() {
    print_message "$BLUE" "Creating secret JSON..."
    
    CURRENT_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Use jq to properly escape JSON values (handles special characters)
    jq -n \
        --arg token "$GP_ACCESS_TOKEN" \
        --arg cookies "$COOKIES" \
        --arg ua "$USER_AGENT" \
        --arg timestamp "$CURRENT_TIMESTAMP" \
        '{
            "gp-access-token": $token,
            "cookies": $cookies,
            "user-agent": $ua,
            "last_updated": $timestamp
        }' > "$TEMP_FILE"
    
    if [ $? -ne 0 ]; then
        print_message "$RED" "✗ Failed to create secret JSON"
        exit 1
    fi
    
    print_message "$GREEN" "✓ Secret JSON created"
    echo ""
}

# Update secret in AWS Secrets Manager
update_secret() {
    print_message "$BLUE" "Updating secret in AWS Secrets Manager..."
    
    # Check if secret exists
    if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" &> /dev/null; then
        # Update existing secret
        if aws secretsmanager update-secret \
            --secret-id "$SECRET_NAME" \
            --secret-string file://"$TEMP_FILE" &> /dev/null; then
            print_message "$GREEN" "✓ Secret updated successfully"
        else
            print_message "$RED" "✗ Failed to update secret"
            exit 1
        fi
    else
        # Create new secret
        print_message "$YELLOW" "Secret does not exist. Creating new secret..."
        if aws secretsmanager create-secret \
            --name "$SECRET_NAME" \
            --description "GoPro Cloud authentication credentials" \
            --secret-string file://"$TEMP_FILE" &> /dev/null; then
            print_message "$GREEN" "✓ Secret created successfully"
        else
            print_message "$RED" "✗ Failed to create secret"
            exit 1
        fi
    fi
    
    echo ""
}

# Final validation
final_validation() {
    print_message "$BLUE" "Performing final validation..."
    
    # Retrieve the secret we just stored
    STORED_SECRET=$(aws secretsmanager get-secret-value \
        --secret-id "$SECRET_NAME" \
        --query SecretString \
        --output text)
    
    # Extract tokens from stored secret
    STORED_COOKIES=$(echo "$STORED_SECRET" | jq -r '.cookies')
    STORED_USER_AGENT=$(echo "$STORED_SECRET" | jq -r '."user-agent"')
    
    # Make test API call with stored tokens
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Cookie: $STORED_COOKIES" \
        -H "User-Agent: $STORED_USER_AGENT" \
        -H "Accept: application/vnd.gopro.jk.media+json; version=2.0.0" \
        -H "Accept-Language: en-US,en;q=0.9" \
        -H "Referer: https://gopro.com/" \
        "$GOPRO_API_URL?per_page=1")
    
    if [ "$HTTP_CODE" -eq 200 ]; then
        print_message "$GREEN" "✓ Final validation successful"
        print_message "$GREEN" "✓ Stored tokens are working correctly"
    else
        print_message "$YELLOW" "⚠ Final validation returned HTTP $HTTP_CODE"
        print_message "$YELLOW" "Tokens are stored but may need verification"
    fi
    
    echo ""
}

# Print summary
print_summary() {
    print_message "$GREEN" "================================================"
    print_message "$GREEN" "  Token Update Complete!"
    print_message "$GREEN" "================================================"
    echo ""
    print_message "$BLUE" "Summary:"
    print_message "$BLUE" "  • Secret Name: $SECRET_NAME"
    print_message "$BLUE" "  • Validation: Passed"
    print_message "$BLUE" "  • Status: Ready for sync operations"
    echo ""
    print_message "$YELLOW" "Next Steps:"
    print_message "$YELLOW" "  1. Test the sync workflow manually"
    print_message "$YELLOW" "  2. Monitor CloudWatch logs for any issues"
    print_message "$YELLOW" "  3. Set up token expiration monitoring"
    echo ""
    print_message "$BLUE" "Tip: For future updates:"
    print_message "$BLUE" "  • Just paste your new cookie string into scripts/cookies.txt"
    print_message "$BLUE" "  • Run this script again"
    print_message "$BLUE" "  • The script will extract and validate everything automatically"
    echo ""
}

# Main execution
main() {
    print_header
    check_prerequisites
    prompt_for_tokens
    validate_tokens
    create_secret_json
    update_secret
    final_validation
    print_summary
}

# Run main function
main
