# Cookie Testing Strategy

## Overview

Since we're using unofficial, reverse-engineered API endpoints, we need an iterative approach to determine which cookies are actually required for authentication.

## Confirmed Required Cookies

Based on real-world testing (2025-11-13):

1. **gp_access_token**
   - Format: Encrypted JWT (JWE)
   - Length: 500-1000+ characters
   - Starts with: `eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ`
   - Example: `<REDACTED_GP_ACCESS_TOKEN>`

2. **gp_user_id**
   - Format: UUID
   - Length: 36 characters with dashes
   - Pattern: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
   - Example: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

## Potentially Required Cookies

These cookies are present in the Cookie header but their necessity is unconfirmed:

- `session` - Session identifier
- `sessionId` - Alternative session identifier
- `_ga` - Google Analytics tracking
- `_gid` - Google Analytics identifier
- Other tracking/analytics cookies

## Testing Approach

### Phase 1: Minimal Authentication (Current)

**Test with**: `gp_access_token` + `gp_user_id` only

**Implementation**:
```python
headers = {
    "Authorization": f"Bearer {gp_access_token}",
    "Cookie": f"gp_access_token={gp_access_token}; gp_user_id={gp_user_id}",
    "User-Agent": user_agent
}
```

**Expected Result**: API calls succeed with 200 responses

**If Fails**: Proceed to Phase 2

### Phase 2: Add Session Cookies

**Test with**: `gp_access_token` + `gp_user_id` + `session` + `sessionId`

**Implementation**:
```python
headers = {
    "Authorization": f"Bearer {gp_access_token}",
    "Cookie": f"gp_access_token={gp_access_token}; gp_user_id={gp_user_id}; session={session}; sessionId={sessionId}",
    "User-Agent": user_agent
}
```

**Expected Result**: API calls succeed with 200 responses

**If Fails**: Proceed to Phase 3

### Phase 3: Full Cookie Header

**Test with**: Complete Cookie header as extracted from browser

**Implementation**:
```python
headers = {
    "Authorization": f"Bearer {gp_access_token}",
    "Cookie": full_cookie_header,  # Everything from browser
    "User-Agent": user_agent
}
```

**Expected Result**: API calls succeed with 200 responses

**If Fails**: API structure may have changed

## Implementation in Token Validator

```python
def validate_tokens(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Validate stored tokens with test API call"""
    
    gp_access_token = credentials.get("gp_access_token")
    gp_user_id = credentials.get("gp_user_id")
    user_agent = credentials.get("user-agent", "Mozilla/5.0...")
    
    # Phase 1: Try minimal cookies first
    auth_headers = {
        "Authorization": f"Bearer {gp_access_token}",
        "Cookie": f"gp_access_token={gp_access_token}; gp_user_id={gp_user_id}",
        "User-Agent": user_agent
    }
    
    try:
        response = requests.get(
            "https://api.gopro.com/media/search",
            headers=auth_headers,
            params={"per_page": 1},
            timeout=10
        )
        
        if response.status_code == 200:
            logger.info("Token validation successful with minimal cookies")
            return {"valid": True, "auth_headers": auth_headers}
            
        # Phase 2: Try with full cookie header if available
        if "full_cookie_header" in credentials:
            logger.info("Retrying with full cookie header")
            auth_headers["Cookie"] = credentials["full_cookie_header"]
            
            response = requests.get(
                "https://api.gopro.com/media/search",
                headers=auth_headers,
                params={"per_page": 1},
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("Token validation successful with full cookies")
                return {"valid": True, "auth_headers": auth_headers}
        
        # If still failing, check for expiration
        if response.status_code in [401, 403]:
            logger.error("Tokens expired or invalid")
            publish_token_expiry_alert()
            raise TokenExpiredError("Manual token refresh required")
        else:
            logger.warning(f"Unexpected response: {response.status_code}")
            raise ValidationError(f"Unexpected API response: {response.status_code}")
            
    except requests.exceptions.Timeout:
        logger.error("Token validation timeout")
        raise ValidationError("API timeout during validation")
```

## Storage Strategy

Store both minimal and full cookie information:

```json
{
  "gp_access_token": "eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ...",
  "gp_user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "full_cookie_header": "gp_access_token=...; gp_user_id=...; session=...; sessionId=...; _ga=...",
  "user-agent": "Mozilla/5.0...",
  "last_updated": "2025-11-13T02:00:00Z"
}
```

This allows:
1. **Minimal approach first**: Faster, cleaner, less data
2. **Fallback to full**: If minimal doesn't work
3. **Debugging**: Can compare what works vs. what doesn't

## Monitoring

Track which approach works in CloudWatch metrics:

- `TokenValidation_MinimalCookies_Success`
- `TokenValidation_FullCookies_Success`
- `TokenValidation_Failed`

This helps identify if GoPro changes their authentication requirements.

## Documentation Updates

As we learn which cookies are actually required:

1. Update TOKEN_EXTRACTION_GUIDE.md with confirmed requirements
2. Update design.md with validated cookie structure
3. Update this document with findings
4. Share findings with community (Reddit, GitHub)

## Community Knowledge

Check these resources for updates:
- https://github.com/itsankoff/gopro-plus (Python implementation)
- https://github.com/mvisonneau/gpcd (Go implementation)
- https://www.reddit.com/r/gopro/comments/12cjv5x/gopro_cloud_api/

If authentication requirements change, community will likely discover it first.

## Risk Mitigation

1. **Store full cookie header**: Always capture complete Cookie header as backup
2. **Log what works**: Track which cookies are actually needed
3. **Alert on changes**: If minimal stops working, alert and try full
4. **Document findings**: Update docs as we learn more
5. **Community engagement**: Share findings to help others

---

**Last Updated**: 2025-11-13  
**Status**: Phase 1 testing pending  
**Next Steps**: Implement Token Validator with fallback logic
