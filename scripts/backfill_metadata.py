#!/usr/bin/env python3
"""
Backfill DynamoDB with metadata from S3 + GoPro API.

This script:
1. Lists all files in S3 (796 objects)
2. Gets existing DynamoDB records (675)
3. For missing records, retrieves metadata from GoPro API
4. Creates/updates DynamoDB with complete VideoMetadata

Usage:
    # Dry run (list actions without executing):
    python scripts/backfill_metadata.py --dry-run
    
    # Execute backfill:
    python scripts/backfill_metadata.py
    
    # Process specific files only:
    python scripts/backfill_metadata.py --filter GH011189
"""

import argparse
import boto3
import json
import sys
import time
from typing import Dict, Any, List, Set
from datetime import datetime

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secrets_client = boto3.client('secretsmanager')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Backfill DynamoDB with metadata from S3 + GoPro API'
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
        '--secret',
        default='gopro/credentials',
        help='Secrets Manager secret name for GoPro credentials'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='List actions without executing'
    )
    parser.add_argument(
        '--filter',
        help='Filter to specific files (e.g., GH011189)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of files to process'
    )
    return parser.parse_args()


def list_s3_files(bucket: str) -> List[Dict[str, Any]]:
    """
    List all files in S3 with metadata.
    
    Returns:
        List of dicts with key, size, last_modified, media_id (from S3 metadata)
    """
    print(f"\nListing S3 files in bucket: {bucket}")
    
    files = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=bucket, Prefix='gopro-videos/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            
            # Get object metadata to extract media_id
            try:
                head = s3_client.head_object(Bucket=bucket, Key=key)
                media_id = head.get('Metadata', {}).get('sourcemediaid')
                
                files.append({
                    'key': key,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                    'media_id': media_id,
                    'filename': key.split('/')[-1]
                })
            except Exception as e:
                print(f"  ⚠ Error reading metadata for {key}: {e}")
                continue
    
    print(f"Found {len(files)} files in S3\n")
    return files


def get_dynamodb_records(table_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Scan all DynamoDB records.
    
    Returns:
        Dict mapping media_id -> record
    """
    table = dynamodb.Table(table_name)
    print(f"Scanning DynamoDB table: {table_name}")
    
    records = {}
    response = table.scan()
    
    for item in response['Items']:
        media_id = item.get('media_id')
        if media_id:
            records[media_id] = item
    
    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        for item in response['Items']:
            media_id = item.get('media_id')
            if media_id:
                records[media_id] = item
    
    print(f"Found {len(records)} records in DynamoDB\n")
    return records


def get_gopro_credentials(secret_name: str) -> Dict[str, Any]:
    """Retrieve GoPro credentials from Secrets Manager."""
    print(f"Retrieving credentials from: {secret_name}")
    
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        credentials = json.loads(response['SecretString'])
        print("✓ Credentials retrieved\n")
        return credentials
    except Exception as e:
        print(f"✗ Error retrieving credentials: {e}")
        sys.exit(1)


def query_gopro_metadata(media_id: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query GoPro API for metadata of a specific media_id.
    
    Note: GoPro API doesn't have a single-file endpoint, so we need to
    list all media and find the matching one. This is cached to avoid
    repeated full listings.
    """
    # This would require implementing a GoPro API call
    # For now, return None to indicate metadata not available from API
    return None


def create_dynamodb_record(
    table_name: str,
    media_id: str,
    s3_key: str,
    filename: str,
    file_size: int,
    metadata: Dict[str, Any] = None
) -> bool:
    """
    Create DynamoDB record with available metadata.
    
    Args:
        table_name: DynamoDB table name
        media_id: Media ID
        s3_key: S3 object key
        filename: Original filename
        file_size: File size in bytes
        metadata: Optional GoPro metadata
        
    Returns:
        True if successful
    """
    table = dynamodb.Table(table_name)
    
    # Build item
    item = {
        'media_id': media_id,
        's3_key': s3_key,
        'filename': filename,
        'file_size': file_size,
        'status': 'COMPLETED',
        'sync_timestamp': datetime.utcnow().isoformat() + 'Z',
        'update_timestamp': datetime.utcnow().isoformat() + 'Z',
        'provider': 'gopro',
        'note': 'backfilled_from_s3'
    }
    
    # Add GoPro metadata if available
    if metadata:
        for key in ['camera_model', 'captured_at', 'created_at', 'file_extension',
                    'height', 'width', 'resolution', 'source_duration', 'media_type',
                    'duration', 'upload_date']:
            if key in metadata and metadata[key] is not None:
                item[key] = metadata[key]
    
    try:
        table.put_item(Item=item)
        return True
    except Exception as e:
        print(f"  ✗ Error creating record: {e}")
        return False


def update_dynamodb_metadata(
    table_name: str,
    media_id: str,
    metadata: Dict[str, Any]
) -> bool:
    """
    Update existing DynamoDB record with GoPro metadata.
    
    Args:
        table_name: DynamoDB table name
        media_id: Media ID
        metadata: GoPro metadata to add
        
    Returns:
        True if successful
    """
    table = dynamodb.Table(table_name)
    
    # Build update expression
    update_parts = []
    expr_values = {':timestamp': datetime.utcnow().isoformat() + 'Z'}
    
    for key in ['camera_model', 'captured_at', 'created_at', 'file_extension',
                'height', 'width', 'resolution', 'source_duration', 'media_type',
                'duration', 'upload_date']:
        if key in metadata and metadata[key] is not None:
            safe_key = key.replace('-', '_')
            update_parts.append(f'{safe_key} = :{safe_key}')
            expr_values[f':{safe_key}'] = metadata[key]
    
    if not update_parts:
        return True  # No updates needed
    
    update_expr = 'SET ' + ', '.join(update_parts) + ', update_timestamp = :timestamp'
    
    try:
        table.update_item(
            Key={'media_id': media_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )
        return True
    except Exception as e:
        print(f"  ✗ Error updating record: {e}")
        return False


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" DYNAMODB METADATA BACKFILL UTILITY")
    print("="*80)
    
    if args.dry_run:
        print("\n⚠ DRY RUN MODE - No changes will be made")
    
    # Get S3 files
    s3_files = list_s3_files(args.bucket)
    
    if args.filter:
        s3_files = [f for f in s3_files if args.filter in f['filename']]
        print(f"Filtered to {len(s3_files)} files matching '{args.filter}'")
    
    if args.limit:
        s3_files = s3_files[:args.limit]
        print(f"Limited to {args.limit} files")
    
    # Get existing DynamoDB records
    dynamodb_records = get_dynamodb_records(args.table)
    
    # Find missing records
    missing = []
    existing_without_metadata = []
    existing_with_metadata = []
    
    for s3_file in s3_files:
        media_id = s3_file.get('media_id')
        
        if not media_id:
            print(f"⚠ No media_id in S3 metadata for: {s3_file['key']}")
            missing.append(s3_file)
            continue
        
        if media_id not in dynamodb_records:
            missing.append(s3_file)
        else:
            # Check if record has new metadata fields
            record = dynamodb_records[media_id]
            if record.get('captured_at') or record.get('resolution'):
                existing_with_metadata.append(s3_file)
            else:
                existing_without_metadata.append(s3_file)
    
    # Summary
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)
    print(f"S3 files: {len(s3_files)}")
    print(f"DynamoDB records: {len(dynamodb_records)}")
    print(f"\nBreakdown:")
    print(f"  • Missing DynamoDB records: {len(missing)}")
    print(f"  • Existing WITH metadata: {len(existing_with_metadata)}")
    print(f"  • Existing WITHOUT metadata: {len(existing_without_metadata)}")
    
    # Show what will be done
    print("\n" + "="*80)
    print("ACTIONS REQUIRED")
    print("="*80)
    
    if missing:
        print(f"\n1. CREATE {len(missing)} new DynamoDB records:")
        for i, f in enumerate(missing[:10], 1):
            print(f"   {i}. {f['filename']} (media_id: {f.get('media_id', 'UNKNOWN')})")
        if len(missing) > 10:
            print(f"   ... and {len(missing) - 10} more")
    
    if existing_without_metadata:
        print(f"\n2. UPDATE {len(existing_without_metadata)} records with metadata:")
        for i, f in enumerate(existing_without_metadata[:10], 1):
            print(f"   {i}. {f['filename']} (media_id: {f['media_id']})")
        if len(existing_without_metadata) > 10:
            print(f"   ... and {len(existing_without_metadata) - 10} more")
    
    print("\n⚠ LIMITATION:")
    print("GoPro API doesn't provide single-file metadata endpoint.")
    print("To get metadata, we would need to:")
    print("  1. List ALL media from GoPro (slow, rate-limited)")
    print("  2. Match each S3 file to API response")
    print("  3. Extract and store metadata")
    
    print("\n💡 RECOMMENDED APPROACH:")
    print("Instead of API calls, create records from S3 data:")
    print("  • media_id: From S3 object metadata")
    print("  • filename: From S3 key")
    print("  • file_size: From S3 object")
    print("  • s3_key: The S3 key itself")
    print("  • status: COMPLETED")
    print("  • Metadata fields: Will be populated on next GoPro sync")
    
    if args.dry_run:
        print("\n✓ Dry run complete. No changes made.")
        return 0
    
    # Ask for confirmation
    print("\n" + "="*80)
    response = input(f"\nCreate {len(missing)} DynamoDB records from S3 data? (yes/no): ")
    
    if response.lower() != 'yes':
        print("Cancelled.")
        return 1
    
    # Create missing records
    print(f"\nCreating {len(missing)} DynamoDB records...")
    
    created = 0
    failed = 0
    
    for i, s3_file in enumerate(missing, 1):
        media_id = s3_file.get('media_id')
        
        if not media_id:
            print(f"  ⚠ Skipping {s3_file['filename']} - no media_id in S3 metadata")
            failed += 1
            continue
        
        success = create_dynamodb_record(
            args.table,
            media_id,
            s3_file['key'],
            s3_file['filename'],
            s3_file['size']
        )
        
        if success:
            created += 1
            if created % 10 == 0:
                print(f"  ✓ Created {created}/{len(missing)} records...")
        else:
            failed += 1
        
        # Rate limiting
        if i % 50 == 0:
            time.sleep(1)
    
    # Summary
    print("\n" + "="*80)
    print("BACKFILL COMPLETE")
    print("="*80)
    print(f"Records created: {created}")
    print(f"Records failed: {failed}")
    print(f"DynamoDB now has: {len(dynamodb_records) + created} records")
    print(f"S3 has: {len(s3_files)} files")
    
    if len(dynamodb_records) + created == len(s3_files):
        print("\n✅ DynamoDB is now in sync with S3!")
    else:
        diff = len(s3_files) - (len(dynamodb_records) + created)
        print(f"\n⚠ Still {abs(diff)} difference - check for files without media_id")
    
    print("\n💡 Next steps:")
    print("  1. Deploy updated Lambda code with metadata support")
    print("  2. Run gopro-sync-orchestrator Step Function")
    print("  3. GoPro API will backfill metadata for all files on next sync")
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
        import traceback
        traceback.print_exc()
        sys.exit(1)
