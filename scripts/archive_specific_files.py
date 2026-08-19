#!/usr/bin/env python3
"""
Archive specific files from S3 to backup bucket.

For files that exist in S3/DynamoDB but not in GoPro API anymore.

Usage:
    # From file list:
    python scripts/archive_specific_files.py --files-list filenames.txt --backup-bucket gopro-archive-dev
    
    # From command line:
    python scripts/archive_specific_files.py --backup-bucket gopro-archive-dev \
      --file GH011196.MP4 --file GOPR1193.JPG --file GOPR1192.JPG
"""

import argparse
import boto3
import sys
from typing import List

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Archive specific files to backup bucket'
    )
    parser.add_argument(
        '--source-bucket',
        default='gopro-dev-<AWS_ACCOUNT_ID>',
        help='Source S3 bucket'
    )
    parser.add_argument(
        '--backup-bucket',
        required=True,
        help='Backup S3 bucket'
    )
    parser.add_argument(
        '--table',
        default='gopro-sync-tracker-dev',
        help='DynamoDB table'
    )
    parser.add_argument(
        '--file',
        action='append',
        dest='files',
        help='Filename to archive (can be used multiple times)'
    )
    parser.add_argument(
        '--files-list',
        help='Text file with one filename per line'
    )
    parser.add_argument(
        '--delete-source',
        action='store_true',
        help='Delete from source after copy'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would happen'
    )
    return parser.parse_args()


def read_files_list(filepath: str) -> List[str]:
    """Read filenames from text file."""
    with open(filepath, 'r') as f:
        return [line.strip() for line in f if line.strip()]


def ensure_backup_bucket(backup_bucket: str):
    """Ensure backup bucket exists."""
    try:
        s3_client.head_bucket(Bucket=backup_bucket)
        print(f"✓ Backup bucket exists: {backup_bucket}\n")
    except:
        print(f"Creating backup bucket: {backup_bucket}...")
        s3_client.create_bucket(
            Bucket=backup_bucket,
            CreateBucketConfiguration={'LocationConstraint': 'us-west-2'}
        )
        s3_client.put_bucket_versioning(
            Bucket=backup_bucket,
            VersioningConfiguration={'Status': 'Enabled'}
        )
        print(f"✓ Created with versioning\n")


def find_file_in_s3(source_bucket: str, filename: str) -> str:
    """Find S3 key for filename."""
    print(f"  Searching for {filename}...")
    
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=source_bucket, Prefix='gopro-videos/'):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith(filename):
                print(f"    Found: {obj['Key']}")
                return obj['Key']
    
    print(f"    NOT FOUND in S3")
    return None


def archive_files(
    source_bucket: str,
    backup_bucket: str,
    filenames: List[str],
    table_name: str,
    delete_source: bool,
    dry_run: bool
):
    """Archive files to backup."""
    
    print(f"\n{'='*80}")
    print(f"Archiving {len(filenames)} files")
    print(f"{'='*80}\n")
    
    if dry_run:
        print("DRY RUN MODE\n")
    
    table = dynamodb.Table(table_name)
    copied = 0
    deleted = 0
    notfound = []
    failed = []
    
    for i, filename in enumerate(filenames, 1):
        print(f"\n{i}/{len(filenames)}: {filename}")
        
        # Find in S3
        s3_key = find_file_in_s3(source_bucket, filename)
        
        if not s3_key:
            notfound.append(filename)
            continue
        
        if dry_run:
            print(f"  [DRY RUN] Would copy to backup")
            if delete_source:
                print(f"  [DRY RUN] Would delete from source")
            continue
        
        # Copy to backup
        try:
            copy_source = {'Bucket': source_bucket, 'Key': s3_key}
            s3_client.copy_object(
                CopySource=copy_source,
                Bucket=backup_bucket,
                Key=s3_key,
                TaggingDirective='COPY',
                MetadataDirective='COPY'
            )
            copied += 1
            print(f"  ✓ Copied to backup")
            
            # Delete from source if requested
            if delete_source:
                s3_client.delete_object(Bucket=source_bucket, Key=s3_key)
                deleted += 1
                print(f"  ✓ Deleted from source")
                
                # Update DynamoDB (find by s3_key)
                try:
                    response = table.scan(
                        FilterExpression='s3_key = :key',
                        ExpressionAttributeValues={':key': s3_key},
                        Limit=1
                    )
                    if response.get('Items'):
                        media_id = response['Items'][0]['media_id']
                        table.update_item(
                            Key={'media_id': media_id},
                            UpdateExpression='SET #note = :note',
                            ExpressionAttributeNames={'#note': 'note'},
                            ExpressionAttributeValues={':note': f'archived_to_{backup_bucket}'}
                        )
                        print(f"  ✓ Updated DynamoDB")
                except Exception as e:
                    print(f"  ⚠ DynamoDB update failed: {e}")
            
        except Exception as e:
            failed.append((filename, str(e)))
            print(f"  ✗ Failed: {e}")
    
    # Summary
    print(f"\n{'='*80}")
    print("ARCHIVE SUMMARY")
    print('='*80)
    print(f"Processed: {len(filenames)} files")
    print(f"Copied: {copied}")
    print(f"Not found: {len(notfound)}")
    print(f"Failed: {len(failed)}")
    if delete_source:
        print(f"Deleted from source: {deleted}")
    
    if notfound:
        print(f"\nNot found in S3:")
        for f in notfound:
            print(f"  • {f}")
    
    if failed:
        print(f"\nFailed:")
        for f, err in failed:
            print(f"  • {f}: {err}")
    
    print()


def main():
    """Main execution."""
    args = parse_args()
    
    # Get filenames
    filenames = []
    if args.files:
        filenames.extend(args.files)
    if args.files_list:
        filenames.extend(read_files_list(args.files_list))
    
    if not filenames:
        print("Error: No files specified. Use --file or --files-list")
        return 1
    
    print("\n" + "="*80)
    print(" ARCHIVE SPECIFIC FILES TO BACKUP")
    print("="*80)
    
    # Ensure backup bucket
    ensure_backup_bucket(args.backup_bucket)
    
    # Archive
    archive_files(
        args.source_bucket,
        args.backup_bucket,
        filenames,
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
