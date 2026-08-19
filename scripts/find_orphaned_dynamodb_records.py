#!/usr/bin/env python3
"""
Find DynamoDB records without matching S3 files.

These records exist in DynamoDB but their S3 files are missing.
This can happen when S3 files are deleted manually or through cleanup.

Usage:
    python scripts/find_orphaned_dynamodb_records.py
    
    # Delete orphaned records:
    python scripts/find_orphaned_dynamodb_records.py --delete
"""

import argparse
import boto3
import sys
from typing import Set, List, Dict, Any

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Find DynamoDB records without matching S3 files'
    )
    parser.add_argument(
        '--bucket',
        default='gopro-dev-<AWS_ACCOUNT_ID>',
        help='S3 bucket name'
    )
    parser.add_argument(
        '--table',
        default='gopro-sync-tracker-dev',
        help='DynamoDB table name'
    )
    parser.add_argument(
        '--delete',
        action='store_true',
        help='Delete orphaned records (requires confirmation)'
    )
    return parser.parse_args()


def get_s3_keys(bucket: str) -> Set[str]:
    """Get set of all S3 keys."""
    print(f"\nListing S3 files in bucket: {bucket}")
    
    keys = set()
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=bucket, Prefix='gopro-videos/'):
        for obj in page.get('Contents', []):
            keys.add(obj['Key'])
    
    print(f"Found {len(keys)} files in S3\n")
    return keys


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


def delete_orphaned_records(table_name: str, records: List[Dict[str, Any]]) -> int:
    """Delete orphaned DynamoDB records."""
    if not records:
        return 0
    
    table = dynamodb.Table(table_name)
    
    print("Records to delete:")
    for i, r in enumerate(records, 1):
        print(f"  {i}. media_id: {r['media_id']}")
        print(f"     s3_key: {r.get('s3_key', 'NONE')}")
        print(f"     filename: {r.get('filename', 'N/A')}")
        print(f"     status: {r.get('status', 'N/A')}")
    
    print(f"\nTotal: {len(records)} orphaned records")
    
    response = input("\nType 'DELETE' to confirm deletion: ")
    if response != 'DELETE':
        print("Cancelled.")
        return 0
    
    print("\nDeleting records...")
    deleted = 0
    
    for i, r in enumerate(records, 1):
        try:
            table.delete_item(Key={'media_id': r['media_id']})
            deleted += 1
            print(f"  ✓ Deleted ({i}/{len(records)}): {r['media_id']}")
        except Exception as e:
            print(f"  ✗ Failed ({i}/{len(records)}): {r['media_id']} - {e}")
    
    return deleted


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" FIND ORPHANED DYNAMODB RECORDS")
    print("="*80)
    
    # Get S3 files
    s3_keys = get_s3_keys(args.bucket)
    
    # Get DynamoDB records
    dynamodb_records = get_dynamodb_records(args.table)
    
    # Find orphaned records (DynamoDB records where S3 file doesn't exist)
    orphaned = []
    
    for record in dynamodb_records:
        s3_key = record.get('s3_key')
        media_id = record.get('media_id')
        
        if not s3_key:
            # Record has no s3_key - likely failed upload
            orphaned.append(record)
            continue
        
        if s3_key not in s3_keys:
            # S3 file doesn't exist
            orphaned.append(record)
    
    # Summary
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)
    print(f"S3 files: {len(s3_keys)}")
    print(f"DynamoDB records: {len(dynamodb_records)}")
    print(f"Orphaned records: {len(orphaned)}")
    print(f"Difference: {len(dynamodb_records) - len(s3_keys)}")
    
    if not orphaned:
        print("\n✅ No orphaned DynamoDB records found!")
        print("✅ All records have matching S3 files.")
        return 0
    
    print("\n" + "="*80)
    print("ORPHANED RECORDS")
    print("="*80)
    
    # Group by reason
    no_s3_key = [r for r in orphaned if not r.get('s3_key')]
    s3_missing = [r for r in orphaned if r.get('s3_key')]
    
    if no_s3_key:
        print(f"\n1. Records WITHOUT s3_key ({len(no_s3_key)}):")
        for i, r in enumerate(no_s3_key[:5], 1):
            print(f"   {i}. media_id: {r['media_id']}")
            print(f"      filename: {r.get('filename', 'N/A')}")
            print(f"      status: {r.get('status', 'N/A')}")
        if len(no_s3_key) > 5:
            print(f"   ... and {len(no_s3_key) - 5} more")
    
    if s3_missing:
        print(f"\n2. Records where S3 file DOESN'T EXIST ({len(s3_missing)}):")
        for i, r in enumerate(s3_missing[:5], 1):
            print(f"   {i}. media_id: {r['media_id']}")
            print(f"      s3_key: {r.get('s3_key')}")
            print(f"      status: {r.get('status', 'N/A')}")
        if len(s3_missing) > 5:
            print(f"   ... and {len(s3_missing) - 5} more")
    
    print("\n💡 THESE RECORDS:")
    print("  • Point to S3 files that no longer exist")
    print("  • Were likely from deleted or manually removed files")
    print("  • Should be cleaned up to match S3 reality")
    
    if args.delete:
        deleted = delete_orphaned_records(args.table, orphaned)
        print(f"\n✅ Deleted {deleted} orphaned records")
        print(f"✅ DynamoDB now matches S3: {len(s3_keys)} files")
    else:
        print("\nTo delete these records, run:")
        print(f"  python scripts/find_orphaned_dynamodb_records.py --delete")
    
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
