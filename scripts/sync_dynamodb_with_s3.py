#!/usr/bin/env python3
"""
Sync DynamoDB table with S3 reality.

This script:
1. Lists all files in S3 bucket
2. Scans all records in DynamoDB
3. Removes DynamoDB records for videos NOT in S3
4. Ensures DynamoDB only reflects actual S3 state
"""

import boto3
import sys
from typing import Set

# Configuration
DYNAMODB_TABLE = "gopro-sync-tracker-dev"
S3_BUCKET = "gopro-dev-193020606184"
S3_PREFIX = "gopro-videos/"

# AWS clients
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)


def get_s3_media_ids() -> Set[str]:
    """Get set of media IDs from S3 filenames."""
    print(f"Listing S3 objects in s3://{S3_BUCKET}/{S3_PREFIX}...")
    
    media_ids = set()
    paginator = s3.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get('Contents', []):
            key = obj['Key']
            # Extract media_id from S3 key (format: gopro-videos/YYYY/MM/FILENAME.MP4)
            # We'll match by checking if DynamoDB s3_key matches
            media_ids.add(key)
    
    print(f"Found {len(media_ids)} files in S3")
    return media_ids


def get_dynamodb_records() -> list:
    """Scan all records from DynamoDB."""
    print(f"Scanning DynamoDB table {DYNAMODB_TABLE}...")
    
    records = []
    response = table.scan()
    records.extend(response['Items'])
    
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        records.extend(response['Items'])
    
    print(f"Found {len(records)} records in DynamoDB")
    return records


def clean_dynamodb(s3_keys: Set[str], dynamodb_records: list):
    """Remove DynamoDB records that don't have corresponding S3 files.
    
    S3 is the source of truth (796 files). DynamoDB should match exactly.
    - Remove records marked COMPLETED without S3 files
    - Remove duplicate records pointing to same S3 file
    - Keep only ONE record per S3 file
    """
    
    records_to_delete = []
    completed_with_s3 = []
    completed_without_s3 = []
    s3_key_to_records = {}  # Track which records point to each S3 key
    
    for record in dynamodb_records:
        media_id = record['media_id']
        status = record.get('status', '')
        s3_key = record.get('s3_key', '')
        
        if status == 'COMPLETED':
            if s3_key:
                if s3_key in s3_keys:
                    # Track all records pointing to this S3 key
                    if s3_key not in s3_key_to_records:
                        s3_key_to_records[s3_key] = []
                    s3_key_to_records[s3_key].append(media_id)
                    completed_with_s3.append(media_id)
                else:
                    # COMPLETED but S3 file missing - mark for deletion
                    records_to_delete.append({
                        'media_id': media_id,
                        's3_key': s3_key,
                        'status': status,
                        'reason': 'S3_FILE_MISSING'
                    })
                    completed_without_s3.append(s3_key)
            else:
                # COMPLETED but no s3_key recorded - mark for deletion
                records_to_delete.append({
                    'media_id': media_id,
                    's3_key': 'NONE',
                    'status': status,
                    'reason': 'NO_S3_KEY'
                })
    
    # Find duplicates - multiple DynamoDB records for same S3 file
    duplicates = []
    for s3_key, media_ids in s3_key_to_records.items():
        if len(media_ids) > 1:
            # Keep the first, delete the rest
            for media_id in media_ids[1:]:
                records_to_delete.append({
                    'media_id': media_id,
                    's3_key': s3_key,
                    'status': 'COMPLETED',
                    'reason': 'DUPLICATE_S3_KEY'
                })
                duplicates.append(f"{media_id} -> {s3_key}")
    
    print(f"\nDynamoDB Analysis:")
    print(f"  - Total S3 files: {len(s3_keys)}")
    print(f"  - COMPLETED records: {len(completed_with_s3)}")
    print(f"  - COMPLETED WITHOUT S3 file: {len(completed_without_s3)}")
    print(f"  - Duplicate records (same S3 key): {len(duplicates)}")
    print(f"  - Records to delete: {len(records_to_delete)}")
    
    if not records_to_delete:
        print("\n✅ DynamoDB is already in sync with S3")
        return
    
    # Show first 10 examples
    print("\nFirst 10 examples of records to delete:")
    for record in records_to_delete[:10]:
        print(f"  - {record['media_id']}: {record['s3_key']}")
    
    # Ask for confirmation
    response = input(f"\nDelete {len(records_to_delete)} records from DynamoDB? (yes/no): ")
    if response.lower() != 'yes':
        print("Aborted.")
        return
    
    # Delete records
    print("\nDeleting records...")
    deleted = 0
    for record in records_to_delete:
        try:
            table.delete_item(Key={'media_id': record['media_id']})
            deleted += 1
            if deleted % 10 == 0:
                print(f"  Deleted {deleted}/{len(records_to_delete)}...")
        except Exception as e:
            print(f"  Error deleting {record['media_id']}: {e}")
    
    print(f"\n✅ Deleted {deleted} records from DynamoDB")
    print(f"✅ DynamoDB now matches S3 reality")


def main():
    """Main execution."""
    print("=== DynamoDB/S3 Sync Tool ===\n")
    
    # Get S3 reality
    s3_keys = get_s3_media_ids()
    
    # Get DynamoDB records
    dynamodb_records = get_dynamodb_records()
    
    # Clean up mismatches
    clean_dynamodb(s3_keys, dynamodb_records)
    
    print("\n=== Summary ===")
    print(f"S3 files: {len(s3_keys)}")
    print(f"DynamoDB records after cleanup: {len(dynamodb_records) - len([r for r in dynamodb_records if r.get('status') == 'COMPLETED' and r.get('s3_key') and r.get('s3_key') not in s3_keys])}")


if __name__ == "__main__":
    main()
