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
    """Remove DynamoDB records that don't have corresponding S3 files."""
    
    records_to_delete = []
    
    for record in dynamodb_records:
        media_id = record['media_id']
        status = record.get('status', '')
        s3_key = record.get('s3_key', '')
        
        # If status is COMPLETED but no S3 file, mark for deletion
        if status == 'COMPLETED' and s3_key and s3_key not in s3_keys:
            records_to_delete.append({
                'media_id': media_id,
                's3_key': s3_key,
                'status': status
            })
    
    print(f"\nFound {len(records_to_delete)} records to clean up:")
    print(f"  - COMPLETED in DynamoDB but missing in S3: {len(records_to_delete)}")
    
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
