#!/usr/bin/env python3
"""
Find duplicate DynamoDB records (multiple media_ids for same S3 key).

This explains why DynamoDB has more records than S3 files - multiple
media_ids are pointing to the same S3 file.

Usage:
    python scripts/find_duplicate_dynamodb_records.py
    
    # Delete duplicate records (keeps first, removes rest):
    python scripts/find_duplicate_dynamodb_records.py --delete
"""

import argparse
import boto3
import sys
from typing import Dict, List, Any
from collections import defaultdict

dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Find duplicate DynamoDB records (same s3_key)'
    )
    parser.add_argument(
        '--table',
        default='gopro-sync-tracker-dev',
        help='DynamoDB table name'
    )
    parser.add_argument(
        '--delete',
        action='store_true',
        help='Delete duplicate records (keeps first, deletes rest)'
    )
    return parser.parse_args()


def get_dynamodb_records(table_name: str) -> List[Dict[str, Any]]:
    """Get all DynamoDB records."""
    table = dynamodb.Table(table_name)
    print(f"Scanning DynamoDB table: {table_name}")
    
    records = []
    response = table.scan()
    records.extend(response['Items'])
    
    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        records.extend(response['Items'])
    
    print(f"Found {len(records)} records in DynamoDB\n")
    return records


def find_duplicates(records: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Find s3_keys with multiple media_ids."""
    
    # Group by s3_key
    s3_key_to_media_ids = defaultdict(list)
    
    for record in records:
        s3_key = record.get('s3_key')
        media_id = record.get('media_id')
        
        if s3_key and media_id:
            s3_key_to_media_ids[s3_key].append({
                'media_id': media_id,
                'filename': record.get('filename'),
                'status': record.get('status'),
                'file_size': record.get('file_size')
            })
    
    # Find duplicates
    duplicates = {}
    for s3_key, media_ids in s3_key_to_media_ids.items():
        if len(media_ids) > 1:
            duplicates[s3_key] = media_ids
    
    return duplicates


def delete_duplicate_records(table_name: str, duplicates: Dict[str, List[Dict[str, Any]]]) -> int:
    """Delete duplicate records (keep first, delete rest)."""
    
    table = dynamodb.Table(table_name)
    
    # Collect records to delete (all except first)
    to_delete = []
    for s3_key, media_ids in duplicates.items():
        for record in media_ids[1:]:  # Skip first, delete rest
            to_delete.append({
                's3_key': s3_key,
                'media_id': record['media_id'],
                'filename': record.get('filename')
            })
    
    print(f"Will keep FIRST media_id for each S3 key, delete the rest:\n")
    for s3_key, media_ids in list(duplicates.items())[:5]:
        print(f"S3 key: {s3_key}")
        print(f"  KEEP: {media_ids[0]['media_id']}")
        for record in media_ids[1:]:
            print(f"  DELETE: {record['media_id']}")
    
    if len(duplicates) > 5:
        print(f"... and {len(duplicates) - 5} more S3 keys with duplicates")
    
    print(f"\nTotal records to delete: {len(to_delete)}")
    
    response = input("\nType 'DELETE' to confirm deletion: ")
    if response != 'DELETE':
        print("Cancelled.")
        return 0
    
    print("\nDeleting duplicate records...")
    deleted = 0
    
    for i, record in enumerate(to_delete, 1):
        try:
            table.delete_item(Key={'media_id': record['media_id']})
            deleted += 1
            if deleted % 10 == 0:
                print(f"  ✓ Deleted {deleted}/{len(to_delete)}...")
        except Exception as e:
            print(f"  ✗ Failed {record['media_id']}: {e}")
    
    print(f"\n✅ Deleted {deleted} duplicate records")
    return deleted


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" FIND DUPLICATE DYNAMODB RECORDS")
    print("="*80)
    print("\nLooking for multiple media_ids pointing to same S3 key...")
    
    # Get all records
    records = get_dynamodb_records(args.table)
    
    # Find duplicates
    duplicates = find_duplicates(records)
    
    if not duplicates:
        print("\n✅ No duplicate records found!")
        print("✅ Each S3 file has exactly one DynamoDB record.")
        return 0
    
    print("\n" + "="*80)
    print("DUPLICATE RECORDS FOUND")
    print("="*80)
    
    total_extra = sum(len(media_ids) - 1 for media_ids in duplicates.values())
    
    print(f"\nFound {len(duplicates)} S3 keys with multiple media_ids")
    print(f"Extra records: {total_extra}")
    
    # Show details
    print("\nDuplicates by S3 key:\n")
    for i, (s3_key, media_ids) in enumerate(list(duplicates.items())[:10], 1):
        filename = s3_key.split('/')[-1]
        print(f"{i}. {filename} ({len(media_ids)} media_ids):")
        for j, record in enumerate(media_ids):
            marker = "KEEP" if j == 0 else "DELETE"
            print(f"   [{marker}] {record['media_id']} (status: {record.get('status', 'N/A')})")
    
    if len(duplicates) > 10:
        print(f"\n... and {len(duplicates) - 10} more S3 keys with duplicates")
    
    print("\n💡 EXPLANATION:")
    print(f"  • {len(duplicates)} S3 files have multiple tracking records")
    print(f"  • {total_extra} duplicate records should be removed")
    print(f"  • This is why DynamoDB has {total_extra} more records than S3 files")
    print("  • Caused by re-uploads before metadata-based detection")
    
    if args.delete:
        deleted = delete_duplicate_records(args.table, duplicates)
        remaining = len(records) - deleted
        print(f"\n✅ DynamoDB now has {remaining} records (matches S3 files)")
    else:
        print("\nTo delete duplicate records, run:")
        print(f"  python scripts/find_duplicate_dynamodb_records.py --delete")
    
    print()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: {e}", file=sys.stderr)
        sys.exit(1)
