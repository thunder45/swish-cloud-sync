# Cloud Sync Application - Spec Update Summary

## Overview

This document summarizes the comprehensive spec update that pivoted the Cloud Sync Application from an OAuth-based authentication design to a cookie-based authentication approach, reflecting the reality that GoPro does not provide an official API.

## What Changed

### Discovery

The original design was based on the incorrect assumption that GoPro provides an official OAuth 2.0 API with a developer portal. Research revealed:

- ❌ No official GoPro Cloud API exists
- ❌ No OAuth 2.0 flow available
- ❌ No automatic token refresh possible
- ✅ Only reverse-engineered, unofficial endpoints work
- ✅ Manual cookie extraction from browser required

### Impact

This discovery required fundamental changes across all three spec documents:
1. **Requirements** (requirements.md)
2. **Design** (design.md)
3. **Implementation Tasks** (tasks.md)

## Updated Documents

### 1. Requirements Document

**File**: `.kiro/specs/cloud-sync-application/requirements.md`

**Key Changes**:
- Added disclaimer about unofficial API in Introduction
- Renamed "Media Authenticator" → "Token Validator" throughout
- Replaced Requirement 2 (OAuth refresh) with token validation requirements
- Added Requirement 11: Manual Token Management (9 acceptance criteria)
- Added Requirement 12: API Resilience (4 acceptance criteria)
- Updated Requirement 13 (formerly 11): Extensibility for Multiple Providers
- Updated glossary with Token Validator and Token Extraction Tool

**Total Requirements**: 13 (was 11)

### 2. Design Document

**File**: `.kiro/specs/cloud-sync-application/design.md`

**Key Changes**:

**Component Updates**:
- Component 1: "Media Authenticator" → "Token Validator"
  - Validates tokens (no refresh capability)
  - Makes test API call to detect expiration
  - Publishes SNS alerts when tokens expire
  - Read-only access to Secrets Manager

**Data Model Updates**:
- Secrets Manager structure changed:
  ```json
  {
    "gp_access_token": "eyJhbGc...",  // JWT from plus.gopro.com
    "gp_user_id": "12345678",          // Numeric user ID
    "user-agent": "Mozilla/5.0...",
    "last_updated": "2025-11-11T02:00:00Z"
  }
  ```
- Removed OAuth-related fields (refresh_token, client_id, etc.)

**Process Updates**:
- Added manual token extraction process
- Added token update script (scripts/update_gopro_tokens.sh)
- Removed automatic rotation Lambda
- Added token validation before each sync

**IAM Updates**:
- Token Validator role: Read-only Secrets Manager access (removed UpdateSecretValue)
- Added SNS publish permission for expiry alerts

**Monitoring Updates**:
- New metrics: TokenValidationSuccess, TokenValidationFailure, TokenExpired
- New alarms: Token expiration, API structure changes
- Updated CloudWatch dashboard

**API Documentation**:
- Documented unofficial endpoints:
  - `GET https://api.gopro.com/media/search`
  - `GET https://api.gopro.com/media/{id}/download`
  - `GET https://api.gopro.com/media/{id}`
- Added warnings about unofficial nature

### 3. Tasks Document

**File**: `.kiro/specs/cloud-sync-application/tasks.md`

**Key Changes**:

**Phase 1 (Infrastructure)**:
- Task 3.1: Create TOKEN_EXTRACTION_GUIDE.md
- Task 3.2: Create token management scripts
- Task 3.3: Manual token extraction (no OAuth setup)
- Task 3.4: VPC infrastructure (renumbered from 3.3)

**Phase 2 (Provider)**:
- Task 4: Updated GoPro provider for cookie-based auth
- Task 4.1: Updated tests for cookie-based approach

**Phase 3 (Lambda Functions)**:
- Task 5: "Media Authenticator" → "Token Validator"
- Task 6: Added API response structure validation

**Phase 4 (Orchestration)**:
- Task 8: "AuthenticateProvider" → "ValidateTokens" state

**Phase 5 (Monitoring)**:
- Task 10: Added token expiration and API change alarms

**Phase 6 (Completely Redesigned)**:
- **Old**: Secrets Rotation
- **New**: Token Health Monitoring and Management
  - Task 13: Token health monitoring
  - Task 13.1: Browser extension (optional)
  - Task 13.2: Token management documentation
  - Task 13.3: API change detection monitoring

**Phase 7 (Deployment)**:
- Task 14: Create reality-based documentation
- Task 14.1: CDK deployment (updated IAM roles)
- Task 14.2: Clean up obsolete OAuth documentation

**Phase 9 (Documentation)**:
- Task 18: Updated incident response for token expiration and API changes

## New Documentation Created

### 1. GOPRO_REALITY_CHECK.md

**Purpose**: Explain the unofficial API situation

**Contents**:
- What doesn't exist (OAuth, developer portal, etc.)
- What actually works (cookie extraction)
- Implementation approach
- Risks and mitigation strategies
- Legal disclaimer

### 2. TOKEN_EXTRACTION_GUIDE.md

**Purpose**: Step-by-step token extraction instructions

**Contents**:
- Chrome extraction method (Application → Cookies)
- Firefox extraction method (Storage → Cookies)
- Required cookies: `gp_access_token` (JWT) and `gp_user_id`
- Token format reference
- Quick reference card
- Troubleshooting guide
- Security best practices

### 3. GOPRO_OAUTH_SETUP.md (Replaced)

**Purpose**: Redirect from obsolete OAuth instructions

**Contents**:
- Obsolescence notice
- Links to correct documentation
- Migration path for anyone following old instructions

### 4. Token Update Script

**File**: `scripts/update_gopro_tokens.sh`

**Purpose**: Simplify token updates in Secrets Manager

**Features**:
- Interactive prompts for tokens
- Token validation with test API call
- AWS Secrets Manager update
- Success/failure reporting

## Authentication Flow Comparison

### Before (OAuth - Incorrect)

```
1. Register app in GoPro Developer Portal ❌ (doesn't exist)
2. Get client_id and client_secret ❌
3. Perform OAuth flow to get refresh_token ❌
4. Lambda automatically refreshes access_token ❌
5. Tokens rotate every 30 days automatically ❌
```

### After (Cookie-Based - Correct)

```
1. User logs into plus.gopro.com in browser ✅
2. User extracts gp_access_token and gp_user_id cookies ✅
3. User runs script to store in Secrets Manager ✅
4. Lambda validates tokens before each sync ✅
5. When tokens expire (401/403), user is alerted ✅
6. User manually re-extracts and updates tokens ✅
```

## Required Cookies (Based on Reverse Engineering)

From community projects (gopro-plus, gpcd):

1. **gp_access_token**
   - Format: JWT (starts with `eyJhbGc...`)
   - Length: 200-500 characters
   - Contains: User identity and authorization claims
   - Location: Cookie on plus.gopro.com

2. **gp_user_id**
   - Format: Numeric identifier
   - Length: 8-10 digits
   - Contains: GoPro account user ID
   - Location: Cookie on plus.gopro.com

3. **User-Agent** (Optional)
   - Can use default if not provided
   - Identifies browser for API calls

## API Endpoints (Unofficial)

Based on reverse engineering:

- **List media**: `GET https://api.gopro.com/media/search?page=1&per_page=100`
- **Download media**: `GET https://api.gopro.com/media/{media_id}/download`
- **Media details**: `GET https://api.gopro.com/media/{media_id}`

**Authentication**: Bearer token in Authorization header
```
Authorization: Bearer {gp_access_token}
```

## Risks and Mitigation

### Risk 1: API Changes Without Notice
- **Mitigation**: API response structure validation, CloudWatch alarms

### Risk 2: Token Expiration
- **Mitigation**: SNS alerts, clear documentation, token validation

### Risk 3: Terms of Service Violation
- **Mitigation**: Rate limiting, respectful usage, user awareness

### Risk 4: Lack of Support
- **Mitigation**: Community knowledge, detailed logging, fallback plans

## Implementation Status

### Completed (Phases 1-6)
- ✅ Infrastructure foundation
- ✅ GoPro provider implementation
- ✅ Lambda functions (with OAuth)
- ✅ Orchestration
- ✅ Monitoring
- ✅ Secrets rotation (now obsolete)

### To Be Updated (Based on New Spec)
- [ ] Phase 1: Token extraction documentation
- [ ] Phase 2: Update GoPro provider for cookies
- [ ] Phase 3: Replace Media Authenticator with Token Validator
- [ ] Phase 3: Add API structure validation to Media Lister
- [ ] Phase 4: Update Step Functions state machine
- [ ] Phase 5: Add token expiration alarms
- [ ] Phase 6: Implement token health monitoring
- [ ] Phase 7: Create reality-based documentation
- [ ] Phase 7: Update IAM roles
- [ ] Phase 9: Update operational documentation

## Next Steps

1. **Review and Approve Spec**: Ensure all stakeholders understand the changes
2. **Update Existing Code**: Modify implemented components to match new spec
3. **Create New Components**: Implement token extraction tooling
4. **Update Documentation**: Replace OAuth docs with cookie-based docs
5. **Test End-to-End**: Verify cookie-based authentication works
6. **Deploy**: Roll out updated system

## References

- Reddit Discussion: https://www.reddit.com/r/gopro/comments/12cjv5x/gopro_cloud_api/
- gopro-plus (Python): https://github.com/itsankoff/gopro-plus
- gpcd (Go): https://github.com/mvisonneau/gpcd
- Community blog: https://thetooth.io/blog/gopro-plus-downloader/

## Legal Disclaimer

This implementation uses unofficial, reverse-engineered API endpoints. Use at your own risk. Always comply with GoPro's Terms of Service.

---

**Document Version**: 1.0  
**Last Updated**: 2025-11-13  
**Status**: Spec update complete, implementation pending
