# GoPro Cloud Token Extraction Guide

## Overview

This guide provides step-by-step instructions for extracting authentication tokens from your GoPro Cloud browser session. These tokens are required for the Cloud Sync Application to access your GoPro videos.

**Important**: This process must be repeated whenever tokens expire (typically every few weeks).

## Prerequisites

- Active GoPro Cloud account with videos
- Chrome or Firefox browser
- AWS CLI configured with appropriate permissions
- Access to the token update script

## Visual Overview: What You're Looking For

Before diving into the detailed steps, here's a quick visual overview of the extraction process:

```
┌─────────────────────────────────────────────────────────────────┐
│                    TOKEN EXTRACTION FLOW                         │
└─────────────────────────────────────────────────────────────────┘

Step 1: Login to GoPro Media Library
   ↓
   [Browser: gopro.com/media-library/]
   
Step 2: Open Developer Tools (F12)
   ↓
   [DevTools: Network Tab]
   
Step 3: Trigger API Requests (scroll/navigate)
   ↓
   [Network Tab: Filter "api.gopro.com"]
   
Step 4: Select API Request
   ↓
   [Request Details: Headers Tab]
   
Step 5: Find Cookie Header
   ↓
   ┌─────────────────────────────────────────────────────────────┐
   │ Cookie: gp_access_token=eyJhbGc...; gp_user_id=7cb49f28...  │
   │         ─────────────────────────   ───────────────────     │
   │              COPY THIS                   COPY THIS          │
   │         (Encrypted JWT)                  (UUID)             │
   └─────────────────────────────────────────────────────────────┘
   
Step 6: Save to temporary file
   ↓
Step 7: Run update script
   ↓
Step 8: Verify tokens work
   ↓
   ✓ Success!
```

## Method 1: Manual Extraction (Chrome)

### Step 1: Log into GoPro Media Library

1. Open Chrome browser
2. Navigate to https://gopro.com/media-library/
3. Click "Sign In" and log in with your GoPro credentials
4. Verify you can see your videos in the media library

**[SCREENSHOT: Chrome browser showing GoPro Media Library with user logged in and videos visible]**

```
Visual Guide:
┌─────────────────────────────────────────────────────────────┐
│ ← → ⟳  https://gopro.com/media-library/          [Profile] │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  GoPro Media Library                                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐                       │
│  │ Video 1 │ │ Video 2 │ │ Video 3 │  ← Your videos here   │
│  │ [thumb] │ │ [thumb] │ │ [thumb] │                       │
│  └─────────┘ └─────────┘ └─────────┘                       │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Step 2: Open Developer Tools

1. Press `F12` or right-click anywhere and select "Inspect"
2. Click on the "Network" tab
3. Ensure "Preserve log" is checked (checkbox at top of Network tab)

**[SCREENSHOT: Chrome DevTools opened with Network tab selected and Preserve log checked]**

```
Visual Guide:
┌─────────────────────────────────────────────────────────────┐
│ Elements Console Sources Network Performance ... │
│          ▼                ▼                                  │
│ ┌───────────────────────────────────────────────────────┐  │
│ │ ☑ Preserve log   Filter: [api.gopro.com]             │  │
│ ├───────────────────────────────────────────────────────┤  │
│ │ Name              Status  Type    Size    Time        │  │
│ │ ─────────────────────────────────────────────────────│  │
│ │ (requests will appear here after scrolling)          │  │
│ └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Step 3: Find Cookies via Network Tab

We'll use the Network tab to see which cookies are actually sent to the API:

1. Click on the "Network" tab in Developer Tools
2. Ensure "Preserve log" is checked
3. In the media library, scroll or navigate to trigger API requests
4. Look for requests to `api.gopro.com` (filter by typing "api.gopro.com")
5. Click on any request to `api.gopro.com/media/` or similar
6. Click on the "Headers" tab in the request details
7. Scroll to "Request Headers" section

**[SCREENSHOT: Network tab showing filtered api.gopro.com requests with one selected]**

```
Visual Guide:
┌─────────────────────────────────────────────────────────────┐
│ Network Tab                                                   │
│ ┌───────────────────────────────────────────────────────┐  │
│ │ Filter: api.gopro.com                                  │  │
│ ├───────────────────────────────────────────────────────┤  │
│ │ Name                    Status  Type    Size           │  │
│ │ ─────────────────────────────────────────────────────│  │
│ │ ► search?page=1         200     xhr     2.3 KB        │  │
│ │ ► media/abc123          200     xhr     1.1 KB  ← Click│  │
│ │ ► download/xyz789       200     media   2.1 MB        │  │
│ └───────────────────────────────────────────────────────┘  │
│                                                               │
│ Request Details:                                             │
│ ┌───────────────────────────────────────────────────────┐  │
│ │ Headers  Preview  Response  Timing  Cookies           │  │
│ │   ▼                                                    │  │
│ │ General                                                │  │
│ │   Request URL: https://api.gopro.com/media/search     │  │
│ │   Request Method: GET                                  │  │
│ │   Status Code: 200 OK                                  │  │
│ │                                                         │  │
│ │ Request Headers                                        │  │
│ │   cookie: gp_access_token=eyJhbGc...; gp_user_id=...  │  │
│ │   user-agent: Mozilla/5.0...                          │  │
│ └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Step 4: Extract Required Headers from Cookie Header

In the Request Headers section, find the **Cookie** header. It will look like:

```
cookie: gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ...; gp_user_id=7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f; session=...; sessionId=...
```

**[SCREENSHOT: Request Headers section with Cookie header highlighted]**

```
Visual Guide - Request Headers Section:
┌─────────────────────────────────────────────────────────────┐
│ Request Headers                                              │
│ ─────────────────────────────────────────────────────────  │
│ :authority: api.gopro.com                                   │
│ :method: GET                                                 │
│ :path: /media/search?page=1&per_page=100                   │
│ :scheme: https                                               │
│ accept: application/json                                     │
│ accept-encoding: gzip, deflate, br                          │
│ accept-language: en-US,en;q=0.9                             │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ cookie: gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVu... ││ ← THIS ONE!
│ │         gp_user_id=7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f;││
│ │         session=abc123...; sessionId=xyz789...          ││
│ └─────────────────────────────────────────────────────────┘│
│ referer: https://gopro.com/media-library/                   │
│ user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X...)     │
└─────────────────────────────────────────────────────────────┘
```

Extract these **2 specific cookies** from the Cookie header:

**gp_access_token**:
```
Look for: gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ...
✅ Copy everything after "gp_access_token=" up to the next semicolon
Format: Encrypted JWT (very long, 500-1000+ characters)
Starts with: eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ
```

**gp_user_id**:
```
Look for: gp_user_id=7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f
✅ Copy everything after "gp_user_id=" up to the next semicolon
Format: UUID (36 characters with dashes)
Example: 7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f
```

**Optional - Additional Cookies**:
If API calls fail with just these two, you may need to include additional cookies from the same Cookie header:
- `session`
- `sessionId`
- Any other cookies present

**Pro Tip**: Right-click the Cookie header value → "Copy value" to get the entire cookie string, then parse out the specific values you need.

### Step 5: Save Tokens Temporarily

Create a temporary text file and paste the extracted values:

```
gp_access_token: [paste encrypted JWT here - starts with eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ...]
gp_user_id: [paste UUID here - format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx]
User-Agent: [paste here or leave blank for default]
Full Cookie Header (optional backup): [paste entire cookie string if needed]
```

### Step 6: Update AWS Secrets Manager

Run the token update script:

```bash
cd /path/to/cloud-sync-application
./scripts/update_gopro_tokens.sh
```

When prompted, paste each token value:
- `gp_access_token`: The JWT string (starts with `eyJhbGc...`)
- `gp_user_id`: Your numeric user ID
- `user-agent`: Press Enter to use default, or paste your browser's user agent

### Step 7: Verify Update

The script will automatically validate your tokens by making a test API call. If successful, you'll see:

```
✓ Token validation successful
✓ Tokens updated successfully in Secrets Manager

You can now trigger a sync execution.
```

## Method 2: Manual Extraction (Firefox)

### Step 1: Log into GoPro Media Library

1. Open Firefox browser
2. Navigate to https://gopro.com/media-library/
3. Click "Sign In" and log in with your GoPro credentials
4. Verify you can see your videos in the media library

**[SCREENSHOT: Firefox browser showing GoPro Media Library with user logged in]**

```
Visual Guide:
┌─────────────────────────────────────────────────────────────┐
│ ← → ⟳  https://gopro.com/media-library/          [Profile] │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  GoPro Media Library                                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐                       │
│  │ Video 1 │ │ Video 2 │ │ Video 3 │  ← Your videos here   │
│  │ [thumb] │ │ [thumb] │ │ [thumb] │                       │
│  └─────────┘ └─────────┘ └─────────┘                       │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Step 2: Open Developer Tools

1. Press `F12` or right-click anywhere and select "Inspect"
2. Click on the "Network" tab
3. Ensure "Persist Logs" is checked

**[SCREENSHOT: Firefox DevTools opened with Network tab selected and Persist Logs checked]**

```
Visual Guide:
┌─────────────────────────────────────────────────────────────┐
│ Inspector Console Debugger Network Performance ...          │
│                            ▼                                 │
│ ┌───────────────────────────────────────────────────────┐  │
│ │ ☑ Persist Logs   Filter URLs: [api.gopro.com]        │  │
│ ├───────────────────────────────────────────────────────┤  │
│ │ File              Status  Type    Size    Time        │  │
│ │ ─────────────────────────────────────────────────────│  │
│ │ (requests will appear here after scrolling)          │  │
│ └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Step 3: Find API Requests

1. In the media library, scroll or navigate to trigger API requests
2. Look for requests to `api.gopro.com`
3. Click on any request to `api.gopro.com/media/` or similar

**[SCREENSHOT: Firefox Network tab showing filtered api.gopro.com requests]**

```
Visual Guide:
┌─────────────────────────────────────────────────────────────┐
│ Network Tab                                                   │
│ ┌───────────────────────────────────────────────────────┐  │
│ │ Filter: api.gopro.com                                  │  │
│ ├───────────────────────────────────────────────────────┤  │
│ │ File                    Status  Type    Size           │  │
│ │ ─────────────────────────────────────────────────────│  │
│ │ ► search?page=1         200     json    2.3 KB        │  │
│ │ ► media/abc123          200     json    1.1 KB  ← Click│  │
│ │ ► download/xyz789       200     video   2.1 MB        │  │
│ └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Step 4: Extract Required Cookies from Cookie Header

1. In the request details, click on the "Headers" tab
2. Find the "Cookie" header in the Request Headers section
3. Extract the same two cookies as described in the Chrome method above:
   - `gp_access_token` (encrypted JWT)
   - `gp_user_id` (UUID)

**[SCREENSHOT: Firefox Headers tab showing Cookie header with tokens]**

```
Visual Guide - Firefox Headers Tab:
┌─────────────────────────────────────────────────────────────┐
│ Headers  Cookies  Response  Timings  Security               │
│   ▼                                                          │
│ Request Headers                                              │
│ ─────────────────────────────────────────────────────────  │
│ Accept: application/json                                     │
│ Accept-Encoding: gzip, deflate, br                          │
│ Accept-Language: en-US,en;q=0.5                             │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ Cookie: gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVu... ││ ← THIS ONE!
│ │         gp_user_id=7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f;││
│ │         session=abc123...; sessionId=xyz789...          ││
│ └─────────────────────────────────────────────────────────┘│
│ Host: api.gopro.com                                          │
│ User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X...)     │
└─────────────────────────────────────────────────────────────┘
```

### Step 5-7: Same as Chrome Method

Follow steps 5-7 from the Chrome method above.

## Token Validation Checklist

Before running the update script, verify your extracted tokens:

```
Pre-Flight Checklist:
┌─────────────────────────────────────────────────────────────┐
│ ☐ gp_access_token starts with: eyJhbGciOiJSU0EtT0FFUCIsImVu │
│ ☐ gp_access_token length: 500-1000+ characters              │
│ ☐ gp_access_token has exactly 4 dots (.) in the string     │
│ ☐ gp_user_id format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  │
│ ☐ gp_user_id length: exactly 36 characters (with dashes)   │
│ ☐ No leading/trailing whitespace in either token           │
│ ☐ No newlines or line breaks in tokens                     │
│ ☐ Tokens copied from api.gopro.com request (not gopro.com) │
│ ☐ Request showed Status 200 (successful)                   │
│ ☐ You're currently logged into GoPro Media Library         │
└─────────────────────────────────────────────────────────────┘

If all boxes checked: ✓ Ready to run update script!
If any box unchecked: ⚠ Review extraction steps again
```

## Token Format Reference

Understanding what valid tokens look like can help you verify you've copied them correctly:

**gp_access_token**: 
- Format: Encrypted JWT (JSON Web Encryption) - typically 500-1000+ characters
- Example: `eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.H1CekSu_HAMyJV6ye-jQb9EDYNvUAE2TiUYyyIg9v3EDZUQPdn3hNx836XUNi9hW4GBBDhVJfWveNWKXKnUEDFrjbrl767rtT99rq7ZV_q5F_TUrvWIHE8-kRUWbmKV0jed7x7dWVHKb6-l8Imu4MEJVF2g7RqhNk87G8I4DC3YuVWU2ScIH1eI0t9sH2Y6wwdcZYDVjQagTw8itRuv2VdDYW007kRwYfQu1qWAy9hspVOYVQ0TSbYVxKSKGZrrKCL8Xl56GGd21KkSxthl7FMb1KAC89bpk0UBtQi38JLHeHfmOv8ZgYImPcNwtYA5SUBVzhfMvzimJy4pveqa9rA.bJk9EtJuzQLpP0m8.Cfc2XcL_j9u1ELf_RboxLOynjiY94ev_AuswMbL0HgFTsnLubA2j2aU1kMwfpk1KbPWrQz6FCvF690hSJ61JuhAjTdsYtFMTPVjzqZLmUVKFQl0QpP4DmjmM1SC_ah2uXgKbqAxe2z7FYhV_p3ACdixzVv0QDvEqGGC9NTnOMxZ7L_E_LszyeLUkt8wk7jkf0u489JhkHipoHkO5TZ-Zw5OEGxPRSoHIXb-KXYsj9E4g6FgM2wDpB7Y36S2QJCrfsRxvTrdjBA460XF8yTwTA2D1XoIrp2RyB6jeFljiPz39LCgIxxGiqKZ-OHEO4lHJyrzz_KDl4oAp3eInD8Ozu8X9kHhyGb9HnWiQ4Vr20OUO6Yv448wkbN-g4q6iS37c_wd1bCto9HVlQr8ll45qyxnzy1vmjUGXPcHpCr0yYj6XpEBjpjbflBebmsatR9xRLvwQP4ZvOaFFn9i2kL7iXFwAy41pO3FzK8Bsuk3w_1DyQC3Iqbz87KR1vybpn-Ktx8jDLAJGF2BArl5L82zgRvsAFKo2W4Q-THWvQhP1Ymu9vUXrNoi62AtbCodGBc6yD2cYEXbmrNBRvjN35RQjz7MWzKqpf_pJeGJ6Tarc9aJxHhJq5brYSTF87OVohxJN1274cVHCqSqEd9fO6S4C9afqmq2FyPrNb1HA_AKH2ScihDk50ZDlYyguSFSFZksmkibmzHoDMr_o5Dqg0kSEGZizi1Y7qqkbAMoqZPrdln04YxF4rlr4ktUK77eXRf485GAdZinIkKowB3pOkIR6KJRI0GIvET0LhxHdWC3XIailRqSXHZtu13v1TvwR7lfPR94bNUuMEbAtNnUwOxM-t8oh9QNz1bztAvP8_GAnAyF6aweF.4CPmZLVvSY2mk-s1tifXPQ`
- Note: Starts with `eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ` and has four dots (`.`) separating five sections
- This is an encrypted JWT (JWE) - treat it like a password!
- Much longer than a standard JWT due to encryption

**gp_user_id**: 
- Format: UUID (Universally Unique Identifier)
- Example: `7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f`
- Note: 36 characters with dashes in format: 8-4-4-4-12
- This is your GoPro account user ID

**User-Agent** (Optional): 
- Format: Full browser identification string, 100-200 characters
- Example: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`
- Note: Can use default if not provided - identifies your browser, OS, and rendering engine

## Quick Reference Card

| What You Need | Where to Find | What to Copy | Expected Format |
|---------------|---------------|--------------|-----------------|
| gp_access_token | Network → api.gopro.com request → Cookie header | Encrypted JWT starting with `eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ` | 500-1000+ chars, 4 dots |
| gp_user_id | Network → api.gopro.com request → Cookie header | UUID value | 36 chars with dashes |
| User-Agent | Network → Request Headers (optional) | Full browser string | 100-200 chars |

**Pro Tip**: Copy the entire Cookie header value, then parse out the specific cookie values you need

## Complete Visual Walkthrough Example

Here's a complete example showing exactly what you should see at each step:

### Example: Finding Tokens in Chrome

```
1. Login Screen
┌─────────────────────────────────────────────────────────────┐
│ https://gopro.com/media-library/                            │
│                                                              │
│  [Your Videos Grid View]                                    │
│  ✓ You should see your videos here                         │
└─────────────────────────────────────────────────────────────┘

2. Open DevTools (Press F12)
┌─────────────────────────────────────────────────────────────┐
│ [Your Videos Grid View]                                     │
├─────────────────────────────────────────────────────────────┤
│ Elements Console Sources Network Performance ...            │
│                            ▼ Click here                     │
└─────────────────────────────────────────────────────────────┘

3. Network Tab Active
┌─────────────────────────────────────────────────────────────┐
│ Network Tab                                                  │
│ ☑ Preserve log  ← Make sure this is checked!               │
│ Filter: [type "api.gopro.com" here]                        │
│                                                              │
│ (Scroll in media library to trigger requests)              │
└─────────────────────────────────────────────────────────────┘

4. After Scrolling - Requests Appear
┌─────────────────────────────────────────────────────────────┐
│ Filter: api.gopro.com                                       │
│ ┌───────────────────────────────────────────────────────┐  │
│ │ Name                    Status  Type    Size           │  │
│ │ search?page=1           200     xhr     2.3 KB        │  │
│ │ media/abc123            200     xhr     1.1 KB  ← Click│  │
│ └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

5. Request Details - Headers Tab
┌─────────────────────────────────────────────────────────────┐
│ Headers  Preview  Response  Timing                          │
│   ▼                                                          │
│ Request Headers                                              │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ cookie: gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVu... ││
│ │         gp_user_id=7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f;││
│ │         session=abc123...; sessionId=xyz789...          ││
│ │         ▲                  ▲                             ││
│ │         │                  │                             ││
│ │         Copy this          Copy this                     ││
│ └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘

6. Right-Click to Copy
┌─────────────────────────────────────────────────────────────┐
│ cookie: gp_access_token=eyJhbGc...                          │
│         │                                                    │
│         └─ Right-click here                                 │
│            ┌──────────────────┐                             │
│            │ Copy value       │ ← Select this               │
│            │ Copy as cURL     │                             │
│            │ Copy as fetch    │                             │
│            └──────────────────┘                             │
└─────────────────────────────────────────────────────────────┘

7. Parse Cookie String
You copied:
gp_access_token=eyJhbGc...; gp_user_id=7cb49f28-...; session=...

Extract:
✓ gp_access_token = eyJhbGc... (everything after = until ;)
✓ gp_user_id = 7cb49f28-... (everything after = until ;)
```

## Method 3: Browser Extension (Future)

A browser extension is planned for future releases that will automate this process. The extension will:

- Automatically detect authenticated GoPro Cloud sessions
- Extract required headers with one click
- Format tokens for direct use with update script
- Provide visual confirmation of successful extraction

**Status**: Not yet implemented. Use manual extraction methods above.

## Troubleshooting

### Troubleshooting Flowchart

```
                    START: Having Issues?
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │ Can you see GoPro Media Library       │
        │ with your videos after login?         │
        └───────────┬───────────────────────────┘
                    │
         ┌──────────┴──────────┐
         │ NO                  │ YES
         ▼                     ▼
    ┌─────────────┐    ┌──────────────────────┐
    │ Check login │    │ DevTools open (F12)? │
    │ credentials │    └──────┬───────────────┘
    └─────────────┘           │
                    ┌─────────┴─────────┐
                    │ NO                │ YES
                    ▼                   ▼
            ┌──────────────┐    ┌──────────────────────┐
            │ Press F12 or │    │ Network tab selected?│
            │ Right-click  │    └──────┬───────────────┘
            │ → Inspect    │           │
            └──────────────┘  ┌────────┴────────┐
                              │ NO              │ YES
                              ▼                 ▼
                      ┌──────────────┐  ┌──────────────────────┐
                      │ Click        │  │ Preserve log checked?│
                      │ Network tab  │  └──────┬───────────────┘
                      └──────────────┘         │
                                     ┌─────────┴─────────┐
                                     │ NO                │ YES
                                     ▼                   ▼
                             ┌──────────────┐    ┌──────────────────┐
                             │ Check the    │    │ See api.gopro.com│
                             │ checkbox     │    │ requests?        │
                             └──────────────┘    └──────┬───────────┘
                                                        │
                                              ┌─────────┴─────────┐
                                              │ NO                │ YES
                                              ▼                   ▼
                                      ┌──────────────┐    ┌──────────────┐
                                      │ Scroll in    │    │ Click request│
                                      │ media library│    │ → Headers tab│
                                      │ to trigger   │    └──────┬───────┘
                                      │ requests     │           │
                                      └──────────────┘           ▼
                                                         ┌──────────────┐
                                                         │ See Cookie   │
                                                         │ header with  │
                                                         │ gp_access_   │
                                                         │ token?       │
                                                         └──────┬───────┘
                                                                │
                                                      ┌─────────┴─────────┐
                                                      │ NO                │ YES
                                                      ▼                   ▼
                                              ┌──────────────┐    ┌──────────────┐
                                              │ Try different│    │ Copy tokens  │
                                              │ api.gopro.com│    │ and run      │
                                              │ request      │    │ update script│
                                              └──────────────┘    └──────────────┘
                                                                          │
                                                                          ▼
                                                                  ┌──────────────┐
                                                                  │   SUCCESS!   │
                                                                  └──────────────┘
```

### Problem: Can't find `gp_access_token` or `gp_user_id` in Cookie header

**Visual Diagnostic**:
```
What you SHOULD see:
┌─────────────────────────────────────────────────────────────┐
│ cookie: gp_access_token=eyJhbGc...; gp_user_id=7cb49f28...  │
│         ✓ Both tokens present                                │
└─────────────────────────────────────────────────────────────┘

What you might see (WRONG):
┌─────────────────────────────────────────────────────────────┐
│ cookie: session=abc123; sessionId=xyz789                    │
│         ✗ Missing gp_access_token and gp_user_id            │
└─────────────────────────────────────────────────────────────┘
```

**Solution**:
1. Ensure you're on `gopro.com/media-library/` and logged in
2. Make sure you're looking at requests to `api.gopro.com` (not other domains)
3. Refresh the page and check Network tab again
4. Try scrolling in the media library to trigger more API requests
5. Try logging out and logging back in
6. Clear browser cache and cookies, then log in fresh

**Common Mistake**: Looking at requests to `gopro.com` instead of `api.gopro.com`
```
WRONG: gopro.com/media-library/    (website requests)
RIGHT: api.gopro.com/media/search  (API requests) ← Look here!
```

### Problem: Token validation fails after extraction

**Visual Diagnostic - Token Format Check**:
```
✓ CORRECT gp_access_token format:
eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.H1CekSu_HAMyJV6ye...
│                                      │
└─ Starts with this exact prefix      └─ Continues for 500-1000+ chars
   (4 dots total in the string)

✗ WRONG - Incomplete copy:
eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.H1CekSu_HAMyJV6ye
│                                                                  │
└─ Missing rest of token (too short)                              ┘

✗ WRONG - Extra whitespace:
  eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.H1CekSu_HAMyJV6ye...
│ │
└─┘ Leading spaces will break validation

✓ CORRECT gp_user_id format:
7cb49f28-0770-4cf0-a3f5-3e4ce9a9301f
│       │    │    │    │           │
└───────┴────┴────┴────┴───────────┘
8 chars-4-4-4-12 chars (36 total with dashes)
```

**Possible causes**:
1. Copied incomplete token (missing characters)
2. Copied extra whitespace or newlines
3. Session expired during extraction
4. Wrong request selected (not authenticated)

**Solution**:
1. Log out and log back into GoPro Cloud
2. Extract tokens again, being careful to copy complete values
3. Ensure you're copying from an authenticated request (should have `gp-access-token` header)
4. Use "Copy value" feature in DevTools (right-click on cookie value)
5. Verify token length: gp_access_token should be 500-1000+ characters

### Problem: Tokens expire quickly

**Explanation**: Token lifespan is unknown and controlled by GoPro. Typical lifespan appears to be 1-4 weeks based on community observations.

**Solution**:
- Set up SNS email alerts to be notified when tokens expire
- Consider extracting tokens proactively every 2 weeks
- Keep this guide bookmarked for quick access

### Problem: Token doesn't start with `eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ`

**Visual Comparison**:
```
✓ CORRECT - Encrypted JWT format:
eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.H1CekSu_HAMyJV6ye...
│                                      │ │                        │
└─ Part 1: Header                     │ └─ Part 2: Encrypted Key │
                                      │
                                      └─ Dot separator (4 total)

✗ WRONG - Regular JWT (not encrypted):
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIi...
│                                  │
└─ Different header (not encrypted)

✗ WRONG - Not a JWT at all:
abc123def456ghi789jkl012mno345pqr678stu901vwx234yz
│
└─ Random string (not base64-encoded JSON)
```

**Solution**:
1. The exact prefix may vary, but it should start with `eyJ` (base64-encoded JSON)
2. Ensure you're copying from `gp_access_token` cookie (not `gp_user_id` or others)
3. The encrypted JWT should be 500-1000+ characters long with exactly 4 dots
4. If the format is completely different, the API structure may have changed

**How to verify you have the right cookie**:
```
In Cookie header, look for:
gp_access_token=eyJhbGc...    ← This is what you want
                │
                └─ Everything after the = sign
```

## Success vs Failure Indicators

### Visual Indicators of Success

```
✓ SUCCESSFUL EXTRACTION - You should see:

1. Network Tab:
   ┌─────────────────────────────────────────────────────────┐
   │ ✓ Multiple api.gopro.com requests visible               │
   │ ✓ Status codes showing 200 (green)                      │
   │ ✓ Type showing "xhr" or "fetch"                         │
   └─────────────────────────────────────────────────────────┘

2. Headers Tab:
   ┌─────────────────────────────────────────────────────────┐
   │ ✓ Cookie header present and visible                     │
   │ ✓ gp_access_token visible in Cookie header              │
   │ ✓ gp_user_id visible in Cookie header                   │
   │ ✓ Token values are long strings (not empty)             │
   └─────────────────────────────────────────────────────────┘

3. Token Format:
   ┌─────────────────────────────────────────────────────────┐
   │ ✓ gp_access_token: 500-1000+ characters                 │
   │ ✓ Starts with: eyJhbGciOiJSU0EtT0FFUCIsImVu...         │
   │ ✓ Contains exactly 4 dots (.)                           │
   │ ✓ gp_user_id: 36 characters with dashes                 │
   │ ✓ Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx          │
   └─────────────────────────────────────────────────────────┘
```

### Visual Indicators of Problems

```
✗ FAILED EXTRACTION - Warning signs:

1. Network Tab Issues:
   ┌─────────────────────────────────────────────────────────┐
   │ ✗ No api.gopro.com requests visible                     │
   │ ✗ Status codes showing 401, 403, or 404 (red)           │
   │ ✗ Only seeing gopro.com requests (not api.gopro.com)    │
   │ ✗ Network tab is empty                                  │
   └─────────────────────────────────────────────────────────┘

2. Headers Tab Issues:
   ┌─────────────────────────────────────────────────────────┐
   │ ✗ No Cookie header visible                              │
   │ ✗ Cookie header exists but missing gp_access_token      │
   │ ✗ Cookie header exists but missing gp_user_id           │
   │ ✗ Token values are empty or very short                  │
   └─────────────────────────────────────────────────────────┘

3. Token Format Issues:
   ┌─────────────────────────────────────────────────────────┐
   │ ✗ gp_access_token: Less than 100 characters             │
   │ ✗ Doesn't start with: eyJ...                            │
   │ ✗ Has wrong number of dots (not 4)                      │
   │ ✗ gp_user_id: Wrong length or no dashes                 │
   │ ✗ Contains spaces, newlines, or special characters      │
   └─────────────────────────────────────────────────────────┘
```

## API Structure Changes

If GoPro changes their API structure, the required headers may change. Signs of API changes:

- Missing expected headers
- Different header names
- 401/403 responses even with fresh tokens
- Unexpected API response formats

**Action**: If you suspect API changes, please:
1. Document what you're seeing
2. Check community resources (Reddit, GitHub issues)
3. Consider alternative backup methods

## Keyboard Shortcuts & Pro Tips

### Keyboard Shortcuts

| Action | Chrome | Firefox |
|--------|--------|---------|
| Open DevTools | `F12` or `Cmd+Option+I` (Mac) / `Ctrl+Shift+I` (Win) | `F12` or `Cmd+Option+I` (Mac) / `Ctrl+Shift+I` (Win) |
| Open Network Tab | `Cmd+Option+I` then click Network | `Cmd+Option+I` then click Network |
| Clear Network Log | Click 🚫 icon or `Cmd+K` | Click 🗑️ icon |
| Search in Network | `Cmd+F` / `Ctrl+F` | `Cmd+F` / `Ctrl+F` |
| Copy Value | Right-click → Copy value | Right-click → Copy value |

### Pro Tips for Faster Extraction

```
💡 TIP 1: Use Filter to Find Requests Quickly
┌─────────────────────────────────────────────────────────────┐
│ Filter: api.gopro.com                                       │
│         ▲                                                    │
│         └─ Type this immediately after opening Network tab  │
└─────────────────────────────────────────────────────────────┘

💡 TIP 2: Right-Click Cookie Header for Quick Copy
┌─────────────────────────────────────────────────────────────┐
│ cookie: gp_access_token=eyJhbGc...                          │
│         ▲                                                    │
│         └─ Right-click → "Copy value" (faster than select)  │
└─────────────────────────────────────────────────────────────┘

💡 TIP 3: Keep DevTools Open While Navigating
┌─────────────────────────────────────────────────────────────┐
│ ☑ Preserve log  ← Check this FIRST                         │
│                                                              │
│ This keeps all requests visible even when navigating        │
└─────────────────────────────────────────────────────────────┘

💡 TIP 4: Use Text Editor with Syntax Highlighting
┌─────────────────────────────────────────────────────────────┐
│ Paste cookie string into VS Code or similar editor          │
│ Easier to see where one cookie ends and another begins      │
│ Can use Find (Cmd+F) to locate specific cookies             │
└─────────────────────────────────────────────────────────────┘

💡 TIP 5: Create a Template File
┌─────────────────────────────────────────────────────────────┐
│ Save this template for quick token extraction:              │
│                                                              │
│ gp_access_token: [PASTE HERE]                               │
│ gp_user_id: [PASTE HERE]                                    │
│ extracted_date: [TODAY'S DATE]                              │
│                                                              │
│ Helps you stay organized and track when tokens extracted    │
└─────────────────────────────────────────────────────────────┘
```

## Security Best Practices

1. **Never share your tokens**: They provide full access to your GoPro Cloud account
2. **Use secure connections**: Only extract tokens over HTTPS
3. **Clear clipboard**: After pasting tokens, clear your clipboard
4. **Delete temporary files**: Remove any text files containing tokens after updating secrets
5. **Rotate regularly**: Consider extracting fresh tokens every 2 weeks proactively
6. **Use private/incognito mode**: Consider extracting tokens in private browsing mode
7. **Log out after extraction**: Log out of GoPro Cloud after extracting tokens

## Token Lifespan

Based on community observations:
- **Typical lifespan**: 1-4 weeks
- **Expiration signs**: 401 or 403 HTTP responses
- **No warning**: Tokens expire without notice
- **No refresh**: Cannot be automatically refreshed

## Automation Considerations

While this process is manual, you can:
- Set calendar reminders to refresh tokens every 2 weeks
- Monitor SNS alerts for expiration notifications
- Keep this guide accessible for quick reference
- Consider scripting the AWS CLI update portion

## Support

For issues with:
- **Token extraction**: Review this guide, check browser console for errors
- **Script execution**: Check AWS CLI configuration, verify IAM permissions
- **API changes**: Check community resources, review CloudWatch logs

## References

- GoPro Cloud: https://gopro.com
- Community discussions: https://www.reddit.com/r/gopro/
- Unofficial implementations: https://github.com/itsankoff/gopro-plus

## Legal Disclaimer

This process uses unofficial, reverse-engineered API endpoints. Use at your own risk. Always comply with GoPro's Terms of Service.
