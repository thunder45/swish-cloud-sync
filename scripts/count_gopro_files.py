#!/usr/bin/env python3
"""
Count GoPro vs Non-GoPro files from API.

Shows breakdown of all 971 items to explain why only ~796 are GoPro camera files.
"""

import boto3
import json
import requests

# Get credentials from Secrets Manager
secrets_client = boto3.client('secretsmanager')
response = secrets_client.get_secret_value(SecretId='gopro/credentials')
credentials = json.loads(response['SecretString'])

cookies = credentials.get('cookies', '')
user_agent = credentials.get('user-agent', 'Mozilla/5.0')

# Statistics
total_items = 0
gopro_files = []
non_gopro_files = []
no_filename = []

print("Fetching ALL media from GoPro API...")
print("=" * 60)

page = 1
while True:
    headers = {
        'Cookie': cookies,
        'User-Agent': user_agent,
        'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://gopro.com/'
    }
    
    response = requests.get(
        "https://api.gopro.com/media/search",
        headers=headers,
        params={'page': page, 'per_page': 100},
        timeout=60
    )
    
    if response.status_code != 200:
        print(f"Error: HTTP {response.status_code}")
        break
    
    data = response.json()
    media_items = data.get('_embedded', {}).get('media', [])
    
    if not media_items:
        break
    
    # Count per page
    for item in media_items:
        total_items += 1
        filename = item.get('filename', '')
        media_id = item.get('id', 'unknown')
        
        if not filename:
            no_filename.append(media_id)
            continue
        
        if filename.startswith('GH') or filename.startswith('GO'):
            gopro_files.append({
                'filename': filename,
                'media_id': media_id,
                'type': 'video' if filename.lower().endswith('.mp4') else 'photo' if filename.lower().endswith('.jpg') else 'other',
                'file_size': item.get('file_size', 0),
                'created_at': item.get('created_at', ''),
                'captured_at': item.get('captured_at', ''),
                'duration': item.get('duration', 0),
                'width': item.get('width', 0),
                'height': item.get('height', 0)
            })
        else:
            non_gopro_files.append({
                'filename': filename,
                'media_id': media_id
            })
    
    # Check pagination
    pages_info = data.get('_pages', {})
    current_page = pages_info.get('current_page', page)
    total_pages = pages_info.get('total_pages', page)
    
    print(f"Page {current_page}/{total_pages}: {len(media_items)} items")
    
    if current_page >= total_pages:
        break
    
    page += 1

# Print summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Total API items: {total_items}")
print(f"")
print(f"GoPro camera files (GH*/GO*): {len(gopro_files)}")

# Breakdown GoPro files by type
gopro_videos = [f for f in gopro_files if f['type'] == 'video']
gopro_photos = [f for f in gopro_files if f['type'] == 'photo']
gopro_other = [f for f in gopro_files if f['type'] == 'other']

print(f"  - Videos (.MP4): {len(gopro_videos)}")
print(f"  - Photos (.jpg): {len(gopro_photos)}")
print(f"  - Other: {len(gopro_other)}")
print(f"")
print(f"Non-GoPro files: {len(non_gopro_files)}")
print(f"No filename: {len(no_filename)}")
print(f"")
print(f"FILTERED OUT: {len(non_gopro_files) + len(no_filename)} items")
print(f"")
print(f"Expected downloads: {len(gopro_files)} GoPro files")

# Show examples of non-GoPro files
if non_gopro_files:
    print(f"\nFirst 10 non-GoPro files (filtered):")
    for f in non_gopro_files[:10]:
        print(f"  - {f['filename']}")

# Show examples of GoPro files
if gopro_files:
    print(f"\nFirst 5 GoPro videos:")
    for f in [f for f in gopro_files if f['type'] == 'video'][:5]:
        print(f"  - {f['filename']}")
    
    print(f"\nFirst 5 GoPro photos:")
    for f in [f for f in gopro_files if f['type'] == 'photo'][:5]:
        print(f"  - {f['filename']}")

# Compare with S3
print("\n" + "=" * 60)
print("COMPARISON WITH S3")
print("=" * 60)

s3_client = boto3.client('s3')
paginator = s3_client.get_paginator('list_objects_v2')

print("Listing S3 files...")
s3_filenames = set()
for s3_page in paginator.paginate(Bucket='gopro-dev-193020606184', Prefix='gopro-videos/'):
    for obj in s3_page.get('Contents', []):
        # Extract filename from S3 key (gopro-videos/YYYY/MM/FILENAME)
        filename = obj['Key'].split('/')[-1]
        s3_filenames.add(filename)

print(f"S3 files: {len(s3_filenames)}")

# Find what's in GoPro but not in S3
gopro_filenames = {f['filename'] for f in gopro_files}
missing_in_s3 = gopro_filenames - s3_filenames
extra_in_s3 = s3_filenames - gopro_filenames

print(f"")
print(f"In GoPro but NOT in S3: {len(missing_in_s3)} files")
print(f"In S3 but NOT in GoPro: {len(extra_in_s3)} files")

if missing_in_s3:
    print(f"\nMissing files (first 20):")
    for filename in sorted(list(missing_in_s3))[:20]:
        file_info = next((f for f in gopro_files if f['filename'] == filename), {})
        print(f"  - {filename} (media_id: {file_info.get('media_id', '?')})")

if extra_in_s3:
    print(f"\nExtra in S3 (first 10):")
    for filename in sorted(list(extra_in_s3))[:10]:
        print(f"  - {filename}")

# Find duplicate filenames in GoPro API
print(f"\n" + "=" * 60)
print("DUPLICATE FILENAMES IN GOPRO API")
print("=" * 60)

from collections import defaultdict
filename_to_media_ids = defaultdict(list)

for file in gopro_files:
    filename_to_media_ids[file['filename']].append(file['media_id'])

duplicates = {fn: ids for fn, ids in filename_to_media_ids.items() if len(ids) > 1}

print(f"Duplicate filenames: {len(duplicates)}")
print(f"Total duplicate media_ids: {sum(len(ids) - 1 for ids in duplicates.values())}")

if duplicates:
    print(f"\nAll duplicate filenames with full metadata:")
    for filename, media_ids in sorted(duplicates.items()):
        print(f"\n  {filename} ({len(media_ids)} copies):")
        for media_id in media_ids:
            # Find full metadata for this media_id
            file_info = next((f for f in gopro_files if f['media_id'] == media_id), {})
            print(f"    - media_id: {media_id}")
            print(f"      created_at: {file_info.get('created_at', 'N/A')}")
            print(f"      captured_at: {file_info.get('captured_at', 'N/A')}")
            print(f"      file_size: {file_info.get('file_size', 0):,} bytes")
            if file_info.get('duration'):
                print(f"      duration: {file_info.get('duration')} sec")
            if file_info.get('width') and file_info.get('height'):
                print(f"      resolution: {file_info.get('width')}x{file_info.get('height')}")

print(f"\n" + "=" * 60)
print(f"EXPECTED RESULT AFTER NEXT SYNC:")
print(f"  S3 files: {len(s3_filenames)} → {len(gopro_filenames)} (+{len(missing_in_s3)})")
print(f"  Note: {len(duplicates)} duplicate filenames will overwrite each other")
print("=" * 60)
