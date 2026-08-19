#!/usr/bin/env python3
"""
Count files in GoPro API that are NOT gopro_media.

This script fetches all media from GoPro API and counts how many
files have gopro_media=false (non-GoPro camera files).

Usage:
    python scripts/count_non_gopro_media.py
    
    # Show details of non-GoPro files:
    python scripts/count_non_gopro_media.py --show-files
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
        description='Count non-GoPro media files from GoPro API'
    )
    parser.add_argument(
        '--secret',
        default='gopro/credentials',
        help='Secrets Manager secret name'
    )
    parser.add_argument(
        '--show-files',
        action='store_true',
        help='Show details of non-GoPro files'
    )
    parser.add_argument(
        '--max-items',
        type=int,
        default=1000,
        help='Maximum items to fetch (default: 1000)'
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


def fetch_all_media(cookies: str, user_agent: str, max_items: int = 1000):
    """
    Fetch all media from GoPro API.
    
    Returns:
        List of all media items
    """
    print(f"\nFetching media from GoPro API (max {max_items} items)...")
    
    all_media = []
    page = 1
    
    headers = {
        'Cookie': cookies,
        'User-Agent': user_agent,
        'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://gopro.com/'
    }
    
    while len(all_media) < max_items:
        try:
            params = {
                'page': page,
                'per_page': 100,  # Max allowed by API
            }
            
            response = requests.get(
                "https://api.gopro.com/media/search",
                headers=headers,
                params=params,
                timeout=60
            )
            
            if response.status_code != 200:
                print(f"Error: HTTP {response.status_code}")
                break
            
            data = response.json()
            media_items = data.get('_embedded', {}).get('media', [])
            
            if not media_items:
                print(f"No more items on page {page}")
                break
            
            all_media.extend(media_items)
            
            # Pagination info
            pages_info = data.get('_pages', {})
            current_page = pages_info.get('current_page', page)
            total_pages = pages_info.get('total_pages', page)
            total_items = pages_info.get('total_items', 0)
            
            print(f"Page {current_page}/{total_pages}: {len(media_items)} items (total: {len(all_media)})")
            
            if current_page >= total_pages:
                print("Reached last page")
                break
            
            if len(all_media) >= max_items:
                print(f"Reached max items limit: {max_items}")
                break
            
            page += 1
            
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break
    
    print(f"\n✓ Fetched {len(all_media)} total items from GoPro API\n")
    return all_media


def analyze_gopro_media(media_items: list, show_files: bool = False):
    """Analyze which files are gopro_media vs not."""
    
    gopro_media_true = []
    gopro_media_false = []
    no_gopro_media_field = []
    
    for item in media_items:
        filename = item.get('filename', 'UNKNOWN')
        gopro_media = item.get('gopro_media')
        
        if gopro_media is True:
            gopro_media_true.append(item)
        elif gopro_media is False:
            gopro_media_false.append(item)
        else:
            no_gopro_media_field.append(item)
    
    # Summary
    print("="*80)
    print("GOPRO_MEDIA ANALYSIS")
    print("="*80)
    print(f"\nTotal items analyzed: {len(media_items)}")
    print(f"\nBreakdown:")
    print(f"  • gopro_media = true:  {len(gopro_media_true)} ({len(gopro_media_true)/len(media_items)*100:.1f}%)")
    print(f"  • gopro_media = false: {len(gopro_media_false)} ({len(gopro_media_false)/len(media_items)*100:.1f}%)")
    print(f"  • gopro_media missing: {len(no_gopro_media_field)} ({len(no_gopro_media_field)/len(media_items)*100:.1f}%)")
    
    # Show non-GoPro files if requested
    if show_files and gopro_media_false:
        print(f"\n{'='*80}")
        print("FILES WITH gopro_media = false")
        print('='*80)
        
        for i, item in enumerate(gopro_media_false, 1):
            filename = item.get('filename', 'UNKNOWN')
            file_type = item.get('type', 'UNKNOWN')
            file_size = item.get('file_size', 0)
            size_mb = file_size / (1024 * 1024) if file_size else 0
            
            print(f"\n{i}. {filename}")
            print(f"   ID: {item.get('id', 'N/A')}")
            print(f"   Type: {file_type}")
            print(f"   Size: {size_mb:.2f} MB")
            print(f"   Camera: {item.get('camera_model', 'N/A')}")
            print(f"   Captured: {item.get('captured_at', 'N/A')}")
    
    if show_files and no_gopro_media_field:
        print(f"\n{'='*80}")
        print("FILES WITHOUT gopro_media field")
        print('='*80)
        
        for i, item in enumerate(no_gopro_media_field[:20], 1):
            filename = item.get('filename', 'UNKNOWN')
            file_type = item.get('type', 'UNKNOWN')
            
            print(f"  {i}. {filename} ({file_type})")
        
        if len(no_gopro_media_field) > 20:
            print(f"  ... and {len(no_gopro_media_field) - 20} more")
    
    print()
    return {
        'gopro_true': len(gopro_media_true),
        'gopro_false': len(gopro_media_false),
        'gopro_missing': len(no_gopro_media_field)
    }


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" GOPRO MEDIA FIELD ANALYZER")
    print("="*80)
    
    # Get credentials
    credentials = get_credentials(args.secret)
    cookies = credentials.get('cookies', '')
    user_agent = credentials.get('user-agent', 'Mozilla/5.0')
    
    # Fetch all media
    media_items = fetch_all_media(cookies, user_agent, args.max_items)
    
    if not media_items:
        print("No media items found!")
        return 1
    
    # Analyze
    results = analyze_gopro_media(media_items, args.show_files)
    
    print("="*80)
    print("SUMMARY")
    print("="*80)
    print(f"\nOut of {len(media_items)} total files:")
    print(f"  • {results['gopro_false']} are NOT GoPro camera files (gopro_media=false)")
    print(f"  • {results['gopro_true']} are GoPro camera files (gopro_media=true)")
    print(f"  • {results['gopro_missing']} don't have the gopro_media field")
    
    print("\n💡 NOTE:")
    print("  Files with gopro_media=false are likely:")
    print("  • Uploaded from phone/computer")
    print("  • Edited versions")
    print("  • Screenshots or other non-camera content")
    print("  • These are filtered out by list_gopro_videos.py (GH*/GO* filename check)")
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
