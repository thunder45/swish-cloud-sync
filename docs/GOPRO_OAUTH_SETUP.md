# ⚠️ DEPRECATED: OAuth Authentication

## Important Notice

**This document is DEPRECATED and no longer accurate.**

GoPro does not provide an official OAuth API for programmatic access to GoPro Cloud. This document was created based on an incorrect assumption.

## Current Authentication Method

The Cloud Sync Application now uses **cookie-based authentication** with manually extracted browser cookies.

## For Current Setup Instructions

**Please refer to:**
- `docs/TOKEN_EXTRACTION_GUIDE.md` - How to extract cookies from browser
- `docs/TASK_3.3_QUICK_START.md` - Quick start for initial setup
- `docs/GOPRO_REALITY_CHECK.md` - Explanation of unofficial API situation
- `docs/COOKIE_TESTING_STRATEGY.md` - Cookie validation strategy

## What Changed

The original implementation plan assumed GoPro provided:
- OAuth 2.0 authorization flow
- Refresh tokens for automatic renewal
- Official API documentation

**Reality:**
- No official API exists
- Must use reverse-engineered endpoints
- Cookies must be manually extracted from browser
- No automatic token refresh possible

## Migration Path

If you were following the old OAuth documentation:

1. **Stop**: OAuth setup won't work
2. **Read**: `docs/GOPRO_REALITY_CHECK.md` for context
3. **Follow**: `docs/TOKEN_EXTRACTION_GUIDE.md` for actual setup
4. **Extract**: Cookies from your browser
5. **Store**: Using `scripts/update_gopro_tokens.sh`

## Questions?

See:
- `docs/TOKEN_EXTRACTION_GUIDE.md` - Detailed extraction guide
- `docs/TASK_3.3_QUICK_START.md` - Quick setup
- `README.md` - Updated project overview

---

**Last Updated**: December 1, 2025  
**Status**: DEPRECATED - Do not use  
**Replacement**: Cookie-based authentication documentation listed above
