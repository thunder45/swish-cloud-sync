# Pagination Debug Guide

**Date:** December 2, 2025  
**Purpose:** Explain pagination behavior and debugging

---

## Questions Answered

### Q1: Why 69 videos instead of 100?

**Answer:** GoPro Provider filters non-GoPro files.

**Flow:**
1. GoPro API returns 100 items (photos + videos from all devices)
2. gopro_provider.py filters to ONLY GoPro camera files (GH*, GO*)
3. Result: ~69 videos (31 items filtered out - likely photos/other devices)

**Code Location:** `lambda_layer/python/cloud_sync_common/gopro_provider.py`

```python
# Only include files starting with GH or GO (GoPro camera naming)
if not (filename.startswith('GH') or filename.startswith('GO')):
    logger.debug(f"Skipping non-GoPro filename: {filename}")
    continue
```

**What's filtered out:**
- Photos (.jpg files): GH010456.jpg → Skipped
- Other device videos: VIRB* → Skipped
- Mobile app uploads: IMG_* → Skipped

---

### Q2: How to see gopro_provider logs?

**Answer:** Lambda Layer code logs to the SAME CloudWatch log group.

**Log Location:** `/aws/lambda/media-lister`

**Provider logs use:** `logger.debug()` and `logger.info()`

**To see provider logs:**
```bash
# All logs including provider
aws logs tail /aws/lambda/media-lister --since 10m --follow

# Filter for provider-specific messages
aws logs tail /aws/lambda/media-lister --since 10m | grep "GoPro\|provider\|Skipping"
```

**Example provider logs:**
```
Listing media from page 1 (max_results=100)
Page 1/33, got 100 items, 971 total items
Skipping non-GoPro filename: GH010456.jpg
Retrieved 69 videos from page 1
```

---

### Q3: Is pagination metadata being used?

**Answer:** YES - it's captured and used by Step Functions.

**Data Flow:**

**1. gopro_provider returns:**
```python
(videos, pagination_metadata)
# pagination_metadata = {'current_page': 1, 'total_pages': 33, 'total_items': 971, 'per_page': 30}
```

**2. media_lister returns:**
```json
{
  "statusCode": 200,
  "new_count": 0,
  "pagination": {
    "current_page": 1,
    "total_pages": 33,
    "total_items": 971,
    "per_page": 30
  }
}
```

**3. State machine captures:**
```python
result_selector={
    "pagination.$": "$.Payload.pagination"  # Captures the _pages data
}
```

**4. State machine uses:**
```python
sfn.Condition.number_less_than_json_path(
    "$.context.current_page",      # 1, 2, 3, ..., 34
    "$.media.pagination.total_pages"  # 33
)
# Returns True until page 34, then False → exit loop
```

**Pagination IS being used** - it controls the loop termination.

---

### Q4: Why can't I see pagination logs?

**Answer:** Timing - old executions used old code.

**Log Enhancement Timeline:**
- **Old code:** `logger.info(f'Pagination metadata: {pagination}')`
- **New code:** `logger.info(f'Pagination metadata from GoPro API: {json.dumps(pagination)}')`
- **Deployed:** 2:00 PM today
- **Last execution:** 12:17 PM (before enhancement)

**To see enhanced logs:** Trigger NEW execution after 2:00 PM deployment.

**What you'll now see:**
```
Pagination metadata from GoPro API: {"current_page": 1, "total_pages": 33, "total_items": 971, "per_page": 30}
Media listing completed successfully - Page 1/33
```

---

### Q5: What about get_pagination_state/update_pagination_state?

**Answer:** OBSOLETE - not used.

**Original Design:** Store page number in DynamoDB  
**Current Implementation:** Step Functions context tracks page

**Step Functions Context:**
```json
{
  "context": {
    "current_page": 1,    // Incremented by IncrementPage state
    "total_synced": 0
  }
}
```

**Why obsolete:**
- Simpler to use Step Functions built-in state
- No DynamoDB writes needed
- Automatic rollback on failure
- Cleaner code

**Status:** Functions removed in latest commit (a8d8d73)

---

## How Pagination Works Now

### Complete Flow

```
Start: context.current_page = 1

┌─> ListMedia(page=1)
│   └─> GoPro API: 100 items
│   └─> Filter: GH*/GO* only → 69 videos
│   └─> DynamoDB check: All 69 COMPLETED
│   └─> Return: new_count=0, pagination={current:1, total:33}
│
├─> CheckNewVideos: new_count=0?
│   └─> YES (0 new videos)
│       └─> IncrementPageNoDownloads
│           └─> context.current_page = 2
│
├─> CheckMorePagesNoDownloads: 2 < 33?
│   └─> YES → Loop back to ListMedia(page=2)
│
├─> ListMedia(page=2)
│   └─> GoPro API: 100 items  
│   └─> Filter: → X videos
│   └─> DynamoDB check: Y new
│   └─> Return: new_count=Y, pagination={current:2, total:33}
│
... continues through all 33 pages ...

└─> ListMedia(page=33)
    └─> IncrementPage → context.current_page = 34
    └─> CheckMorePages: 34 < 33?
        └─> NO → GenerateSummaryComplete → COMPLETE
```

---

## Debugging Current Execution

### Check State Machine Progress

**Console:** https://console.aws.amazon.com/states/home?region=us-east-1

Look for:
- Current state name
- Input/Output of each state
- Execution timeline

### Check Lambda Logs

**Media Lister logs:**
```bash
aws logs tail /aws/lambda/media-lister --since 5m --follow
```

**Look for:**
- "Listing media from provider (page=X)"
- "Pagination metadata from GoPro API: {...}"
- "Page X/Y" in completion message
- Filtered video count

**Video Downloader logs:**
```bash
aws logs tail /aws/lambda/video-downloader --since 5m --follow
```

**Look for:**
- "Updated sync status to COMPLETED"
- Video downloads (should be 0 for early pages if all synced)

### Verify DynamoDB State

```bash
# Count COMPLETED
aws dynamodb scan --table-name gopro-sync-tracker-dev \
  --filter-expression "#s = :status" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":status":{"S":"COMPLETED"}}' \
  --select COUNT | jq '.Count'

# Should match S3 file count (796)
```

---

## Expected Log Output (New Execution)

### Page 1 (All Synced):
```
Listing media from GoPro Cloud (page 1)
Listing media from provider (page=1)
Retrieved 69 videos from page 1
Pagination metadata from GoPro API: {"current_page": 1, "total_pages": 33, "total_items": 971, "per_page": 30}
Batch querying DynamoDB for 69 items
Retrieved 69 sync statuses from DynamoDB
Filtered 0 new videos from 69 total
Found 0 new videos to sync
Media listing completed successfully - Page 1/33
```

### State Machine:
```
CheckNewVideos: new_count=0
→ IncrementPageNoDownloads: page 1→2
→ CheckMorePagesNoDownloads: 2 < 33? YES
→ Loop back to ListMedia(page=2)
```

### Page 27 (Example with New Videos):
```
Listing media from GoPro Cloud (page 27)
Retrieved 73 videos from page 27
Pagination metadata: {"current_page": 27, "total_pages": 33, ...}
Filtered 15 new videos from 73 total
Found 15 new videos to sync
Media listing completed successfully - Page 27/33
```

### State Machine:
```
CheckNewVideos: new_count=15
→ DownloadVideos (5 concurrent)
→ IncrementPage: page 27→28, total_synced += 15
→ CheckMorePagesAfterDownloads: 28 < 33? YES
→ Loop to page 28
```

---

## Current System State

**DynamoDB:** 796 COMPLETED records (matches S3)  
**Lambda Env Vars:** `DYNAMODB_TABLE=gopro-sync-tracker-dev` ✅  
**State Machine:** Fixed to continue on new_count=0 ✅  
**Logging:** Enhanced with pagination visibility ✅

**Deployed:** 2:00 PM today  
**Last test:** 12:17 PM (before fixes)

---

## Next Steps

**To test all fixes:**

1. **Go to AWS Console:**  
   https://console.aws.amazon.com/states/home?region=us-east-1

2. **Start new execution** of `gopro-sync-orchestrator`

3. **Monitor CloudWatch Logs:**
   ```bash
   aws logs tail /aws/lambda/media-lister --since 1m --follow
   ```

4. **Expected:**
   - Page 1: 69 videos, 0 new → Continue
   - Page 2-33: Process each
   - Stop at page 34 (34 >= 33)
   - Download only truly new videos (~175)

**Result:** 796 existing + ~175 new = ~971 total in DynamoDB/S3
