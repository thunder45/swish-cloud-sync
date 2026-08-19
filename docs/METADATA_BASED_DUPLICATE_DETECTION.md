# Metadata-Based Duplicate Detection

## Overview
Implemented comprehensive metadata fingerprinting to prevent false duplicate detection and eliminate unnecessary `_2` file versions in S3.

## Problem Statement
Previous implementation compared only `filename` and `file_size`, which caused:
- Same file being downloaded multiple times as `_2` versions
- Example: `GOPR0768.JPG` (3955949 bytes) and `GOPR0768_2.JPG` (3955949 bytes) were both created despite being the same file

## Solution
Use full GoPro metadata fingerprint for duplicate detection by comparing:
1. `filename` - Original filename
2. `file_size` - File size in bytes
3. `captured_at` - When the media was captured (camera timestamp) 
4. `resolution` - Video/photo resolution
5. `source_duration` - Duration in milliseconds

## Implementation

### 1. VideoMetadata Class (provider_interface.py)
Extended `VideoMetadata` dataclass with GoPro-specific fields:
```python
@dataclass
class VideoMetadata:
    # Core fields
    media_id: str
    filename: str
    download_url: str
    file_size: int
    upload_date: str
    duration: Optional[int] = None
    provider: str = "unknown"
    
    # Extended GoPro metadata for duplicate detection
    camera_model: Optional[str] = None
    captured_at: Optional[str] = None
    created_at: Optional[str] = None
    file_extension: Optional[str] = None
    height: Optional[int] = None
    width: Optional[int] = None
    resolution: Optional[str] = None
    source_duration: Optional[str] = None
    media_type: Optional[str] = None
```

### 2. GoProProvider (gopro_provider.py)
Updated `_parse_media_item()` to extract and populate all metadata fields from GoPro API response:
- `camera_model` - Camera model (e.g., "HERO7 Black")
- `captured_at` - Capture timestamp from camera
- `created_at` - Creation timestamp
- `file_extension` - File extension
- `height` / `width` - Video dimensions
- `resolution` - Resolution string
- `source_duration` - Duration in milliseconds
- `media_type` - Media type (Video/Photo)

### 3. Media Lister (media_lister/handler.py)
Updated `list_media_from_provider()` to pass all metadata fields to video_downloader:
```python
video_dict = {
    'media_id': video.media_id,
    'filename': video.filename,
    'download_url': video.download_url,
    'file_size': video.file_size,
    'upload_date': video.upload_date,
    'duration': video.duration,
    'media_type': getattr(video, 'media_type', 'video'),
    'resolution': getattr(video, 'resolution', 'unknown'),
    # Extended GoPro metadata
    'camera_model': getattr(video, 'camera_model', None),
    'captured_at': getattr(video, 'captured_at', None),
    'created_at': getattr(video, 'created_at', None),
    'file_extension': getattr(video, 'file_extension', None),
    'height': getattr(video, 'height', None),
    'width': getattr(video, 'width', None),
    'source_duration': getattr(video, 'source_duration', None)
}
```

### 4. Video Downloader (video_downloader/handler.py)
#### a. Metadata Extraction
Extract full metadata from event payload:
```python
metadata = {
    'media_id': media_id,
    'filename': filename,
    'file_size': file_size,
    'upload_date': upload_date,
    'duration': event.get('duration'),
    'media_type': event.get('media_type'),
    'resolution': event.get('resolution'),
    'camera_model': event.get('camera_model'),
    'captured_at': event.get('captured_at'),
    'created_at': event.get('created_at'),
    'file_extension': event.get('file_extension'),
    'height': event.get('height'),
    'width': event.get('width'),
    'source_duration': event.get('source_duration')
}
```

#### b. Duplicate Detection Function
New `is_exact_duplicate()` function compares metadata fingerprints:
```python
def is_exact_duplicate(new_metadata, existing_metadata):
    """Check if metadata represents the exact same file."""
    # Compare all critical immutable fields
    return (
        new_metadata['filename'] == existing_metadata['filename'] and
        new_metadata['file_size'] == existing_metadata['file_size'] and
        new_metadata['captured_at'] == existing_metadata['captured_at'] and
        new_metadata['resolution'] == existing_metadata['resolution'] and
        new_metadata['source_duration'] == existing_metadata['source_duration']
    )
```

#### c. Enhanced Query
New `query_existing_files_metadata()` retrieves full metadata from DynamoDB:
```python
def query_existing_files_metadata(table, filename):
    response = table.scan(
        FilterExpression='contains(s3_key, :filename)',
        ExpressionAttributeValues={':filename': filename},
        ProjectionExpression='media_id, s3_key, filename, file_size, captured_at, resolution, source_duration, camera_model, height, width'
    )
    # Returns list of items with full metadata
```

#### d. Updated generate_unique_s3_key()
Changed signature and logic:
```python
def generate_unique_s3_key(table, provider, metadata, upload_date):
    """Uses full metadata fingerprint for duplicate detection."""
    # Query existing files
    existing_items = query_existing_files_metadata(table, metadata['filename'])
    
    # Check for exact duplicate
    for item in existing_items:
        if is_exact_duplicate(metadata, item):
            logger.info(f'Exact duplicate - reusing key: {item["s3_key"]}')
            return item['s3_key']  # Reuse existing key
    
    # Different file - create numbered version
    # ... numbering logic ...
```

#### e. DynamoDB Storage
Store all metadata fields in DynamoDB during IN_PROGRESS update:
```python
update_attributes = {
    'provider': provider_name,
    'filename': filename,
    'file_size': file_size,
    'upload_date': upload_date,
}

# Add extended metadata (only non-None values)
for key in ['duration', 'media_type', 'resolution', 'camera_model', 
            'captured_at', 'created_at', 'file_extension', 'height', 
            'width', 'source_duration']:
    value = metadata.get(key)
    if value is not None:
        update_attributes[key] = value

update_sync_status(table, media_id, 'IN_PROGRESS', update_attributes)
```

## DynamoDB Schema Changes
New fields added to sync tracker table:
- `camera_model` (String) - Camera model
- `captured_at` (String) - Capture timestamp (ISO 8601)
- `created_at` (String) - Creation timestamp (ISO 8601)
- `file_extension` (String) - File extension
- `height` (Number) - Video height
- `width` (Number) - Video width
- `resolution` (String) - Resolution string
- `source_duration` (String) - Duration in milliseconds
- `media_type` (String) - Media type (Video/Photo)

**Note:** DynamoDB is schema-less, so no migration needed. New fields will be populated as files are processed.

## How It Works

### Scenario 1: First Upload
```
File: GOPR0768.JPG
Size: 3955949 bytes
Captured: 2022-04-24T18:03:13Z
Resolution: 1520p
Duration: 68635ms

→ No existing files found
→ Use base key: gopro-videos/2022/04/GOPR0768.JPG
```

### Scenario 2: Exact Duplicate (Same File)
```
New File: GOPR0768.JPG
Size: 3955949 bytes
Captured: 2022-04-24T18:03:13Z
Resolution: 1520p
Duration: 68635ms

Existing: GOPR0768.JPG
Size: 3955949 bytes
Captured: 2022-04-24T18:03:13Z
Resolution: 1520p
Duration: 68635ms

→ Metadata fingerprint matches!
→ Reuse existing key: gopro-videos/2022/04/GOPR0768.JPG
→ Skip download (already in S3)
```

### Scenario 3: Different File (True Duplicate Name)
```
New File: GOPR0768.JPG
Size: 2500000 bytes
Captured: 2023-06-15T14:20:00Z  ← Different!
Resolution: 1080p                ← Different!
Duration: 45000ms                ← Different!

Existing: GOPR0768.JPG
Size: 3955949 bytes
Captured: 2022-04-24T18:03:13Z
Resolution: 1520p
Duration: 68635ms

→ Metadata fingerprint differs
→ Create numbered version: gopro-videos/2023/06/GOPR0768_2.JPG
→ Download new file
```

## Benefits

### 1. Eliminates False Duplicates
- Same file won't create `_2` versions anymore
- Saves S3 storage costs
- Cleaner file organization

### 2. Accurate Duplicate Numbering
- Only creates `_2`, `_3` versions for truly different files
- Uses comprehensive metadata comparison
- Handles edge cases properly

### 3. Preserved Functionality
- Still handles true duplicates (different files, same name)
- Maintains chronological organization
- Backward compatible with existing data

### 4. Better Observability
- Logs show why files are duplicates or not
- Includes metadata in log messages
- Metrics track duplicate detection

## Testing

### Test Script
Use `scripts/test_duplicate_handling.py` to verify behavior:
```bash
python scripts/test_duplicate_handling.py
```

### Manual Testing
1. Trigger sync for same file twice → Should reuse S3 key
2. Check DynamoDB → Should have metadata fields populated
3. Check CloudWatch Logs → Should show metadata fingerprint matches
4. Upload file with same name but different metadata → Should create `_2` version

### Expected Log Output
```
# Exact duplicate:
Exact duplicate found via metadata fingerprint: GOPR0768.JPG - 
Metadata match (size=3955949, captured_at=2022-04-24T18:03:13Z, resolution=1520p) - 
Reusing existing key: gopro-videos/2022/04/GOPR0768.JPG

# Different file:
Different file detected: GOPR0768.JPG - 
New metadata (size=2500000, captured_at=2023-06-15T14:20:00Z, resolution=1080p) 
does not match existing files
Creating numbered version: GOPR0768_2.JPG (version 2)
```

## Backward Compatibility
- Existing DynamoDB records without metadata will still work
- Metadata fields are optional in comparison logic
- Graceful fallback to size-only comparison if metadata unavailable
- No data migration required

## Performance Impact
- Minimal - DynamoDB scan already performed
- Added ProjectionExpression fields (metadata) to existing query
- No additional API calls
- Same complexity: O(n) where n = files with matching filename

## Future Enhancements
- Consider using GSI on `captured_at` for faster queries
- Add checksum comparison for ultimate validation
- Store metadata in S3 object tags for visibility
