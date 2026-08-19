#!/usr/bin/env python3
"""
Find S3 files without media_id in metadata.

These files exist in S3 but can't be tracked in DynamoDB because
they're missing the SourceMediaId metadata field.

Usage:
    python scripts/find_orphaned_s3_files.py
    
    # Delete orphaned files:
    python scripts/find_orphaned_s3_files.py --delete
"""

import argparse
import boto3
import sys

s3_client = boto3.client('s3')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Find S3 files without media_id metadata'
    )
    parser.add_argument(
        '--bucket',
        default='gopro-dev-<AWS_ACCOUNT_ID>',
        help='S3 bucket name'
    )
    parser.add_argument(
        '--delete',
        action='store_true',
        help='Delete orphaned files (requires confirmation)'
    )
    return parser.parse_args()


def find_files_without_media_id(bucket: str):
    """Find all S3 files missing SourceMediaId metadata."""
    print(f"\nScanning S3 bucket: {bucket}")
    print("Looking for files without SourceMediaId metadata...\n")
    
    orphaned_files = []
    checked = 0
    
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=bucket, Prefix='gopro-videos/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            checked += 1
            
            try:
                # Get object metadata
                head = s3_client.head_object(Bucket=bucket, Key=key)
                media_id = head.get('Metadata', {}).get('sourcemediaid')
                
                if not media_id:
                    orphaned_files.append({
                        'key': key,
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'],
                        'filename': key.split('/')[-1]
                    })
                    
            except Exception as e:
                print(f"  ⚠ Error checking {key}: {e}")
            
            if checked % 100 == 0:
                print(f"  Checked {checked} files...")
    
    print(f"\nChecked {checked} total files")
    print(f"Found {len(orphaned_files)} files without media_id\n")
    
    return orphaned_files


def delete_orphaned_files(bucket: str, files: list) -> int:
    """Delete orphaned S3 files."""
    if not files:
        return 0
    
    print("Files to delete:")
    for i, f in enumerate(files, 1):
        size_mb = f['size'] / (1024 * 1024)
        print(f"  {i}. {f['key']} ({size_mb:.2f} MB)")
    
    total_size = sum(f['size'] for f in files)
    total_size_mb = total_size / (1024 * 1024)
    print(f"\nTotal: {len(files)} files, {total_size_mb:.2f} MB")
    
    response = input("\nType 'DELETE' to confirm deletion: ")
    if response != 'DELETE':
        print("Cancelled.")
        return 0
    
    print("\nDeleting files...")
    deleted = 0
    
    for i, f in enumerate(files, 1):
        try:
            s3_client.delete_object(Bucket=bucket, Key=f['key'])
            deleted += 1
            print(f"  ✓ Deleted ({i}/{len(files)}): {f['key']}")
        except Exception as e:
            print(f"  ✗ Failed ({i}/{len(files)}): {f['key']} - {e}")
    
    return deleted


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" FIND ORPHANED S3 FILES (No SourceMediaId)")
    print("="*80)
    
    # Find orphaned files
    orphaned = find_files_without_media_id(args.bucket)
    
    if not orphaned:
        print("✅ All S3 files have media_id metadata!")
        print("✅ DynamoDB can be synced with all files.")
        return 0
    
    print("="*80)
    print("ORPHANED FILES FOUND")
    print("="*80)
    
    for i, f in enumerate(orphaned, 1):
        size_mb = f['size'] / (1024 * 1024)
        print(f"\n{i}. {f['filename']}")
        print(f"   Key: {f['key']}")
        print(f"   Size: {size_mb:.2f} MB")
        print(f"   Last Modified: {f['last_modified']}")
    
    total_size = sum(f['size'] for f in orphaned)
    total_size_mb = total_size / (1024 * 1024)
    
    print(f"\nTotal: {len(orphaned)} files without media_id ({total_size_mb:.2f} MB)")
    
    print("\n💡 THESE FILES:")
    print("  • Exist in S3 but have no SourceMediaId metadata")
    print("  • Cannot be tracked in DynamoDB")
    print("  • Were likely uploaded manually or from old code")
    print("  • Can be safely deleted OR manually tagged with media_id")
    
    if args.delete:
        deleted = delete_orphaned_files(args.bucket, orphaned)
        print(f"\n✅ Deleted {deleted} orphaned files")
    else:
        print("\nTo delete these files, run:")
        print(f"  python scripts/find_orphaned_s3_files.py --delete")
    
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
