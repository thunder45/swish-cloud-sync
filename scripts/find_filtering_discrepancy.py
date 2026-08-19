#!/usr/bin/env python3
"""
Find files that are gopro_media=true but don't match GH*/GO* filename filter.

This explains the discrepancy between count_non_gopro_media (836) and
list_gopro_videos (818) - 18 files difference.

Usage:
    python scripts/find_filtering_discrepancy.py
"""

import argparse
import json
import requests
import boto3
import sys

secrets_client = boto3.client('secretsmanager')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Find gopro_media=true files that don\'t match filename filter'
    )
    parser.add_argument(
        '--secret',
        default='gopro/credentials',
        help='Secrets Manager secret name'
    )
    return parser.parse_args()


def get_credentials(secret_name: str):
    """Get GoPro credentials from Secrets Manager."""
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        credentials = json.loads(response['SecretString'])
        return credentials
    except Exception as e:
        print(f"Error retrieving credentials: {e}")
        sys.exit(1)


def fetch_all_media(cookies: str, user_agent: str):
    """Fetch all media from GoPro API."""
    print("\nFetching all media from GoPro API...")
    
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
        params = {'page': page, 'per_page': 100}
        
        response = requests.get(
            "https://api.gopro.com/media/search",
            headers=headers,
            params=params,
            timeout=60
        )
        
        if response.status_code != 200:
            break
        
        data = response.json()
        media_items = data.get('_embedded', {}).get('media', [])
        
        if not media_items:
            break
        
        all_media.extend(media_items)
        
        pages_info = data.get('_pages', {})
        current_page = pages_info.get('current_page', page)
        total_pages = pages_info.get('total_pages', page)
        
        print(f"  Page {current_page}/{total_pages}: {len(all_media)} total items")
        
        if current_page >= total_pages:
            break
        
        page += 1
    
    print(f"\n✓ Fetched {len(all_media)} items\n")
    return all_media


def find_discrepancy(media_items: list):
    """Find files that are gopro_media=true but don't match GH*/GO* filter."""
    
    # Count by filtering logic
    gopro_media_true = []
    matches_filename_filter = []
    gopro_true_but_no_match = []
    
    for item in media_items:
        filename = item.get('filename', '')
        gopro_media = item.get('gopro_media')
        
        # Check if gopro_media=true
        if gopro_media is True:
            gopro_media_true.append(item)
            
            # Check if matches GH*/GO* filter
            if filename and (filename.startswith('GH') or filename.startswith('GO')):
                matches_filename_filter.append(item)
            else:
                # This is the discrepancy!
                gopro_true_but_no_match.append(item)
    
    # Results
    print("="*80)
    print("FILTERING DISCREPANCY ANALYSIS")
    print("="*80)
    
    print(f"\nTotal items: {len(media_items)}")
    print(f"gopro_media = true: {len(gopro_media_true)}")
    print(f"Matches GH*/GO* filter: {len(matches_filename_filter)}")
    print(f"\n⚠ Discrepancy: {len(gopro_true_but_no_match)} files")
    
    print("\nThese files have gopro_media=true but don't start with GH or GO:")
    print("="*80)
    
    if gopro_true_but_no_match:
        for i, item in enumerate(gopro_true_but_no_match, 1):
            filename = item.get('filename', 'UNKNOWN')
            file_type = item.get('type', 'UNKNOWN')
            file_size = item.get('file_size', 0)
            size_mb = file_size / (1024 * 1024) if file_size else 0
            camera = item.get('camera_model', 'N/A')
            
            print(f"\n{i}. {filename}")
            print(f"   ID: {item.get('id')}")
            print(f"   Type: {file_type}")
            print(f"   Size: {size_mb:.2f} MB")
            print(f"   Camera: {camera}")
            print(f"   gopro_media: {item.get('gopro_media')}")
    
    print("\n" + "="*80)
    print("EXPLANATION")
    print("="*80)
    print(f"\nThe current filter (GH*/GO*) excludes {len(gopro_true_but_no_match)} files that:")
    print("  • Are marked as gopro_media=true by GoPro API")
    print("  • But have different filename patterns")
    print("\nFilename patterns found:")
    
    # Analyze filename patterns
    patterns = {}
    for item in gopro_true_but_no_match:
        filename = item.get('filename', '')
        if filename:
            prefix = filename[:2] if len(filename) >= 2 else filename
            patterns[prefix] = patterns.get(prefix, 0) + 1
    
    for prefix, count in sorted(patterns.items(), key=lambda x: -x[1]):
        print(f"  • {prefix}*: {count} files")
    
    print("\n💡 RECOMMENDATION:")
    print("  Consider updating the filter to include these patterns if they are")
    print("  legitimate GoPro camera files (check camera_model field).")
    print()
    
    return gopro_true_but_no_match


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" GOPRO FILENAME FILTER DISCREPANCY FINDER")
    print("="*80)
    
    # Get credentials
    credentials = get_credentials(args.secret)
    cookies = credentials.get('cookies', '')
    user_agent = credentials.get('user-agent', 'Mozilla/5.0')
    
    # Fetch all media
    media_items = fetch_all_media(cookies, user_agent)
    
    # Find discrepancy
    discrepancy_files = find_discrepancy(media_items)
    
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
