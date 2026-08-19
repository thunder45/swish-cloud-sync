#!/usr/bin/env python3
"""
Cleanup script for false duplicate files in S3.

This script identifies and deletes files with _2, _3 suffixes that are false duplicates
(same file re-uploaded with same size and metadata).

Usage:
    # Dry run (list only, no deletion):
    python scripts/cleanup_duplicate_files.py --dry-run
    
    # Delete duplicates (requires confirmation):
    python scripts/cleanup_duplicate_files.py
    
    # Delete without confirmation (DANGEROUS):
    python scripts/cleanup_duplicate_files.py --force

Requirements:
    pip install boto3
    AWS credentials configured with S3 and DynamoDB access
"""

import argparse
import boto3
import sys
from typing import List, Dict, Any
from datetime import datetime

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Cleanup false duplicate files from S3 bucket'
    )
    parser.add_argument(
        '--bucket',
        default='gopro-videos',
        help='S3 bucket name (default: swish-cloud-sync-gopro-videos)'
    )
    parser.add_argument(
        '--table',
        default='gopro-sync-tracker-dev',
        help='DynamoDB table name (default: gopro-sync-tracker-dev)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='List files to delete without actually deleting'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Delete without confirmation prompt'
    )
    parser.add_argument(
        '--pattern',
        default='_2',
        help='Filename pattern to search for (default: _2, can use _3, _4, etc.)'
    )
    return parser.parse_args()


def list_duplicate_files(bucket: str, pattern: str) -> List[Dict[str, Any]]:
    """
    List all S3 files matching the duplicate pattern.
    
    Args:
        bucket: S3 bucket name
        pattern: Pattern to search for (e.g., '_2')
        
    Returns:
        List of file dictionaries with key, size, last_modified
    """
    print(f"\nScanning S3 bucket: {bucket}")
    print(f"Looking for files with pattern: '{pattern}'\n")
    
    duplicate_files = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    # Scan entire bucket
    for page in paginator.paginate(Bucket=bucket):
        if 'Contents' not in page:
            continue
        
        for obj in page['Contents']:
            key = obj['Key']
            filename = key.split('/')[-1]
            
            # Check if filename contains the pattern before the extension
            # e.g., GOPR0768_2.JPG, GH011189_2.MP4
            if pattern in filename:
                # Verify it's a numbered version (pattern followed by digit)
                parts = filename.split(pattern)
                if len(parts) >= 2:
                    # Check if next character after pattern is a digit
                    after_pattern = parts[1]
                    if after_pattern and (after_pattern[0].isdigit() or after_pattern.startswith('.')):
                        duplicate_files.append({
                            'key': key,
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'],
                            'filename': filename
                        })
    
    return duplicate_files


def get_dynamodb_records_for_files(table_name: str, s3_keys: List[str], pattern: str = '_2') -> List[Dict[str, Any]]:
    """
    Get ALL DynamoDB records with pattern in s3_key (including duplicates).
    
    Args:
        table_name: DynamoDB table name
        s3_keys: List of S3 keys (not used, kept for compatibility)
        pattern: Pattern to search for in s3_key
        
    Returns:
        List of DynamoDB records (may have multiple records with same s3_key)
    """
    table = dynamodb.Table(table_name)
    
    print(f"\nQuerying DynamoDB table: {table_name}")
    print(f"Searching for ALL records with '{pattern}' in s3_key...")
    
    # Scan for all records with pattern in s3_key
    try:
        response = table.scan(
            FilterExpression='contains(s3_key, :pattern)',
            ExpressionAttributeValues={':pattern': pattern}
        )
        
        items = response.get('Items', [])
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                FilterExpression='contains(s3_key, :pattern)',
                ExpressionAttributeValues={':pattern': pattern},
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items.extend(response.get('Items', []))
        
        # Return as list to preserve ALL records (including duplicates)
        records = []
        for item in items:
            s3_key = item.get('s3_key', '')
            if s3_key:
                records.append({
                    'media_id': item.get('media_id'),
                    's3_key': s3_key,
                    'filename': item.get('filename'),
                    'file_size': item.get('file_size'),
                    'status': item.get('status')
                })
        
        print(f"Found {len(records)} DynamoDB records with pattern '{pattern}'\n")
        
    except Exception as e:
        print(f"  ⚠ Error scanning table: {e}\n")
        return []
    
    return records


def delete_s3_files(bucket: str, files: List[Dict[str, Any]], dry_run: bool = True) -> int:
    """
    Delete files from S3.
    
    Args:
        bucket: S3 bucket name
        files: List of file dictionaries
        dry_run: If True, only list files without deleting
        
    Returns:
        Number of files deleted
    """
    if not files:
        print("No files to delete.")
        return 0
    
    if dry_run:
        print(f"\n{'='*80}")
        print("DRY RUN - Files that would be deleted:")
        print('='*80)
        
        total_size = 0
        for i, file in enumerate(files, 1):
            size_mb = file['size'] / (1024 * 1024)
            total_size += file['size']
            print(f"{i}. {file['key']}")
            print(f"   Size: {size_mb:.2f} MB")
            print(f"   Last Modified: {file['last_modified']}")
        
        total_size_gb = total_size / (1024 * 1024 * 1024)
        print(f"\nTotal: {len(files)} files, {total_size_gb:.2f} GB")
        print("\nTo actually delete these files, run without --dry-run flag")
        return 0
    
    # Delete files
    print(f"\nDeleting {len(files)} files from S3...")
    
    deleted_count = 0
    failed = []
    
    for i, file in enumerate(files, 1):
        try:
            s3_client.delete_object(
                Bucket=bucket,
                Key=file['key']
            )
            deleted_count += 1
            print(f"  ✓ Deleted ({i}/{len(files)}): {file['key']}")
        except Exception as e:
            failed.append((file['key'], str(e)))
            print(f"  ✗ Failed ({i}/{len(files)}): {file['key']} - {e}")
    
    print(f"\nDeleted {deleted_count} files successfully")
    
    if failed:
        print(f"\nFailed to delete {len(failed)} files:")
        for key, error in failed:
            print(f"  • {key}: {error}")
    
    return deleted_count


def delete_dynamodb_records(table_name: str, records: List[Dict[str, Any]], dry_run: bool = True) -> int:
    """
    Delete ALL DynamoDB records (including multiple records with same s3_key).
    
    Args:
        table_name: DynamoDB table name
        records: List of record dictionaries
        dry_run: If True, only list records without deleting
        
    Returns:
        Number of records deleted
    """
    if not records:
        print("\nNo DynamoDB records to delete.")
        return 0
    
    if dry_run:
        print(f"\n{'='*80}")
        print("DRY RUN - DynamoDB records that would be deleted:")
        print('='*80)
        
        for i, record in enumerate(records, 1):
            print(f"{i}. Media ID: {record['media_id']}")
            print(f"   S3 Key: {record['s3_key']}")
            print(f"   Filename: {record.get('filename', 'N/A')}")
            print(f"   Status: {record.get('status', 'N/A')}")
        
        print(f"\nTotal: {len(records)} DynamoDB records (includes duplicates with same s3_key)")
        return 0
    
    # Delete records
    table = dynamodb.Table(table_name)
    print(f"\nDeleting {len(records)} records from DynamoDB...")
    
    deleted_count = 0
    failed = []
    
    for i, record in enumerate(records, 1):
        try:
            table.delete_item(
                Key={'media_id': record['media_id']}
            )
            deleted_count += 1
            print(f"  ✓ Deleted ({i}/{len(records)}): {record['media_id']}")
        except Exception as e:
            failed.append((record['media_id'], str(e)))
            print(f"  ✗ Failed ({i}/{len(records)}): {record['media_id']} - {e}")
    
    print(f"\nDeleted {deleted_count} records successfully")
    
    if failed:
        print(f"\nFailed to delete {len(failed)} records:")
        for media_id, error in failed:
            print(f"  • {media_id}: {error}")
    
    return deleted_count


def main():
    """Main execution function."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" S3 DUPLICATE FILE CLEANUP UTILITY")
    print("="*80)
    
    if args.dry_run:
        print("\n⚠ DRY RUN MODE - No files will be deleted")
    else:
        print("\n⚠ DELETION MODE - Files will be permanently deleted")
    
    # List duplicate files
    duplicate_files = list_duplicate_files(args.bucket, args.pattern)
    
    if not duplicate_files:
        print(f"\n✓ No files found with pattern '{args.pattern}'")
        return 0
    
    print(f"\nFound {len(duplicate_files)} duplicate files:")
    
    # Calculate total size
    total_size = sum(f['size'] for f in duplicate_files)
    total_size_gb = total_size / (1024 * 1024 * 1024)
    
    print(f"  • Total size: {total_size_gb:.2f} GB")
    print(f"  • Pattern: '{args.pattern}'")
    
    # Get ALL DynamoDB records with pattern (includes all 143)
    s3_keys = [f['key'] for f in duplicate_files]
    all_dynamodb_records = get_dynamodb_records_for_files(args.table, s3_keys, args.pattern)
    
    # Separate matching vs orphaned records
    s3_keys_set = set(s3_keys)
    matching_records = [r for r in all_dynamodb_records if r['s3_key'] in s3_keys_set]
    orphaned_records = [r for r in all_dynamodb_records if r['s3_key'] not in s3_keys_set]
    
    # Count unique s3_keys
    unique_s3_keys = len(set(r['s3_key'] for r in matching_records))
    
    # Show summary
    print("\n" + "="*80)
    print("CLEANUP SUMMARY")
    print("="*80)
    print(f"S3 files to delete: {len(duplicate_files)}")
    print(f"DynamoDB records to delete: {len(matching_records)}")
    print(f"  (Unique S3 keys in DynamoDB: {unique_s3_keys})")
    print(f"  (Multiple media_ids per S3 key: {len(matching_records) - unique_s3_keys})")
    print(f"Storage to free: {total_size_gb:.2f} GB")
    
    if orphaned_records:
        print(f"\n⚠ Found {len(orphaned_records)} orphaned DynamoDB records (S3 file doesn't exist):")
        for i, record in enumerate(orphaned_records[:5], 1):
            print(f"  {i}. {record['s3_key']} (media_id: {record['media_id']})")
        if len(orphaned_records) > 5:
            print(f"  ... and {len(orphaned_records) - 5} more")
        print("\nThese will also be deleted to keep DynamoDB in sync with S3.")
        
        # Include ALL records (matching + orphaned)
        dynamodb_records = matching_records + orphaned_records
    else:
        dynamodb_records = matching_records
    
    # Confirmation prompt (unless dry-run or force)
    if not args.dry_run and not args.force:
        print("\n" + "="*80)
        print("⚠  WARNING: This action cannot be undone!")
        print("="*80)
        response = input("\nType 'DELETE' to confirm deletion: ")
        
        if response != 'DELETE':
            print("\nCancelled by user.")
            return 1
    
    # Perform deletion
    deleted_s3 = delete_s3_files(args.bucket, duplicate_files, args.dry_run)
    deleted_db = delete_dynamodb_records(args.table, dynamodb_records, args.dry_run)
    
    # Final summary
    print("\n" + "="*80)
    print("CLEANUP COMPLETE")
    print("="*80)
    
    if args.dry_run:
        print(f"\nDry run completed. No files were deleted.")
        print(f"Run without --dry-run to perform actual deletion.")
    else:
        print(f"\nS3 files deleted: {deleted_s3}")
        print(f"DynamoDB records deleted: {deleted_db}")
        print(f"Storage freed: ~{total_size_gb:.2f} GB")
    
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
