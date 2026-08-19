#!/usr/bin/env python3
"""
Archive old files to backup S3 bucket.

This script:
1. Queries GoPro API for files captured before specified date
2. Creates backup S3 bucket if needed
3. Copies files from main bucket to backup
4. Optionally removes from main bucket after successful copy

Usage:
    # Copy files captured before June 2020 to backup:
    python scripts/archive_old_files.py --before 2020-06-01 --backup-bucket gopro-archive-dev
    
    # Delete from main after copy:
    python scripts/archive_old_files.py --before 2020-06-01 --backup-bucket gopro-archive-dev --delete-source
"""

import argparse
import json
import requests
import boto3
import sys
from typing import List, Dict, Any

secrets_client = boto3.client('secretsmanager')
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Archive old GoPro files to backup bucket'
    )
    parser.add_argument(
        '--secret',
        default='gopro/credentials',
        help='Secrets Manager secret name'
    )
    parser.add_argument(
        '--source-bucket',
        default='gopro-dev-<AWS_ACCOUNT_ID>',
        help='Source S3 bucket'
    )
    parser.add_argument(
        '--backup-bucket',
        required=True,
        help='Backup S3 bucket name (will be created if doesn\'t exist)'
    )
    parser.add_argument(
        '--table',
        default='gopro-sync-tracker-dev',
        help='DynamoDB table name'
    )
    parser.add_argument(
        '--before',
        help='Archive files captured before this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--after',
        help='Archive files captured after this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--delete-source',
        action='store_true',
        help='Delete from source bucket after successful copy'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be archived without doing it'
    )
    return parser.parse_args()


def get_credentials(secret_name: str):
    """Get GoPro credentials."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])


def fetch_gopro_media(cookies: str, user_agent: str) -> List[Dict[str, Any]]:
    """Fetch all gopro_media=true files from API."""
    print("\nFetching media from GoPro API...")
    
    all_media = []
    page = 1
    
    headers = {
        'Cookie': cookies,
        'User-Agent': user_agent,
        'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://gopro.com/'
    }
    
    while True:
        response = requests.get(
            "https://api.gopro.com/media/search",
            headers=headers,
            params={'page': page, 'per_page': 100},
            timeout=60
        )
        
        if response.status_code != 200:
            break
        
        data = response.json()
        media_items = data.get('_embedded', {}).get('media', [])
        
        if not media_items:
            break
        
        for item in media_items:
            if item.get('gopro_media') is True:
                all_media.append(item)
        
        pages_info = data.get('_pages', {})
        current_page = pages_info.get('current_page', page)
        total_pages = pages_info.get('total_pages', page)
        
        print(f"  Page {current_page}/{total_pages}: {len(all_media)} files")
        
        if current_page >= total_pages:
            break
        page += 1
    
    print(f"✓ Fetched {len(all_media)} files\n")
    return all_media


def filter_by_date(media_items: List[Dict[str, Any]], before: str = None, after: str = None):
    """Filter by captured_at date."""
    matching = []
    
    for item in media_items:
        captured_at = item.get('captured_at')
        if not captured_at:
            continue
        
        captured_date = captured_at[:10]
        
        if before and captured_date >= before:
            continue
        if after and captured_date < after:
            continue
        
        matching.append(item)
    
    return matching


def ensure_backup_bucket_exists(backup_bucket: str) -> bool:
    """Create backup bucket if it doesn't exist."""
    try:
        s3_client.head_bucket(Bucket=backup_bucket)
        print(f"✓ Backup bucket exists: {backup_bucket}\n")
        return True
    except:
        print(f"Creating backup bucket: {backup_bucket}...")
        try:
            s3_client.create_bucket(
                Bucket=backup_bucket,
                CreateBucketConfiguration={'LocationConstraint': 'us-west-2'}
            )
            
            # Enable versioning
            s3_client.put_bucket_versioning(
                Bucket=backup_bucket,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            
            print(f"✓ Created backup bucket with versioning\n")
            return True
        except Exception as e:
            print(f"✗ Error creating backup bucket: {e}")
            return False


def copy_files_to_backup(
    source_bucket: str,
    backup_bucket: str,
    files_to_archive: List[Dict[str, Any]],
    table_name: str,
    delete_source: bool = False,
    dry_run: bool = False
):
    """Copy files to backup bucket."""
    
    print(f"{'='*90}")
    print(f"Files to archive: {len(files_to_archive)}")
    print(f"{'='*90}\n")
    
    # Show files
    for i, item in enumerate(files_to_archive, 1):
        filename = item.get('filename')
        captured = item.get('captured_at', 'N/A')[:10]
        size = item.get('file_size', 0)
        size_mb = size / (1024**2)
        print(f"{i}. {filename} (captured: {captured}, size: {size_mb:.2f} MB)")
    
    if dry_run:
        print(f"\n✓ DRY RUN complete. No files copied.")
        return
    
    print(f"\n⚠  WARNING: This will copy {len(files_to_archive)} files to backup")
    if delete_source:
        print(f"⚠  And DELETE them from source bucket!")
    
    response = input("\nType 'ARCHIVE' to confirm: ")
    if response != 'ARCHIVE':
        print("Cancelled.")
        return
    
    # Get DynamoDB table
    table = dynamodb.Table(table_name)
    
    # Copy files
    print(f"\nCopying files to {backup_bucket}...")
    copied = 0
    failed = []
    
    for item in files_to_archive:
        media_id = item.get('id')
        filename = item.get('filename')
        captured_at = item.get('captured_at', '')
        
        # Find S3 key from DynamoDB
        try:
            db_response = table.get_item(Key={'media_id': media_id})
            if 'Item' not in db_response:
                failed.append((filename, "No DynamoDB record"))
                continue
            
            source_key = db_response['Item'].get('s3_key')
            if not source_key:
                failed.append((filename, "No s3_key in record"))
                continue
            
            # Copy to backup with same key structure
            copy_source = {'Bucket': source_bucket, 'Key': source_key}
            
            s3_client.copy_object(
                CopySource=copy_source,
                Bucket=backup_bucket,
                Key=source_key,
                TaggingDirective='COPY',
                MetadataDirective='COPY'
            )
            
            copied += 1
            print(f"  ✓ Copied ({copied}/{len(files_to_archive)}): {filename}")
            
            # Delete from source if requested
            if delete_source:
                s3_client.delete_object(Bucket=source_bucket, Key=source_key)
                # Update DynamoDB
                table.update_item(
                    Key={'media_id': media_id},
                    UpdateExpression='SET #note = :note',
                    ExpressionAttributeNames={'#note': 'note'},
                    ExpressionAttributeValues={':note': f'archived_to_{backup_bucket}'}
                )
                print(f"    • Deleted from source")
            
        except Exception as e:
            failed.append((filename, str(e)))
            print(f"  ✗ Failed: {filename} - {e}")
    
    # Summary
    print(f"\n{'='*90}")
    print("ARCHIVE COMPLETE")
    print('='*90)
    print(f"Copied: {copied} files")
    if failed:
        print(f"Failed: {len(failed)} files")
    if delete_source:
        print(f"Deleted from source: {copied} files")
    print()


def main():
    """Main execution."""
    args = parse_args()
    
    if not args.before and not args.after:
        print("Error: Must specify --before and/or --after")
        return 1
    
    print("\n" + "="*90)
    print(" ARCHIVE OLD GOPRO FILES TO BACKUP BUCKET")
    print("="*90)
    
    if args.dry_run:
        print("\n⚠ DRY RUN MODE\n")
    
    # Get credentials
    credentials = get_credentials(args.secret)
    
    # Fetch from GoPro API
    all_media = fetch_gopro_media(
        credentials.get('cookies'), 
        credentials.get('user-agent', 'Mozilla/5.0')
    )
    
    # Filter by date
    matching = filter_by_date(all_media, args.before, args.after)
    
    print(f"Found {len(matching)} files matching date criteria\n")
    
    if not matching:
        print("No files to archive.")
        return 0
    
    # Ensure backup bucket exists
    if not ensure_backup_bucket_exists(args.backup_bucket):
        return 1
    
    # Copy files
    copy_files_to_backup(
        args.source_bucket,
        args.backup_bucket,
        matching,
        args.table,
        args.delete_source,
        args.dry_run
    )
    
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
