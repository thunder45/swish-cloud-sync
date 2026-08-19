#!/usr/bin/env python3
"""
Query GoPro API for files by capture date.

This queries the GoPro API directly (not DynamoDB) to find files
captured before/after specific dates using the captured_at field.

Usage:
    # Files captured before June 2020:
    python scripts/query_gopro_by_date.py --before 2020-06-01
    
    # Files captured after a date:
    python scripts/query_gopro_by_date.py --after 2021-01-01
    
    # Files captured between dates:
    python scripts/query_gopro_by_date.py --after 2020-01-01 --before 2021-01-01
"""

import argparse
import json
import requests
import boto3
import sys
from typing import List, Dict, Any

secrets_client = boto3.client('secretsmanager')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Query GoPro API for files by capture date'
    )
    parser.add_argument(
        '--secret',
        default='gopro/credentials',
        help='Secrets Manager secret name'
    )
    parser.add_argument(
        '--before',
        help='Show files captured before this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--after',
        help='Show files captured after this date (YYYY-MM-DD)'
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


def fetch_all_gopro_media(cookies: str, user_agent: str) -> List[Dict[str, Any]]:
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
            print(f"Error: HTTP {response.status_code}")
            break
        
        data = response.json()
        media_items = data.get('_embedded', {}).get('media', [])
        
        if not media_items:
            break
        
        # Only keep gopro_media=true files
        for item in media_items:
            if item.get('gopro_media') is True:
                all_media.append(item)
        
        pages_info = data.get('_pages', {})
        current_page = pages_info.get('current_page', page)
        total_pages = pages_info.get('total_pages', page)
        
        print(f"  Page {current_page}/{total_pages}: {len(all_media)} GoPro files so far")
        
        if current_page >= total_pages:
            break
        
        page += 1
    
    print(f"\n✓ Fetched {len(all_media)} GoPro camera files\n")
    return all_media


def filter_by_date(media_items: List[Dict[str, Any]], before_date: str = None, after_date: str = None) -> List[Dict[str, Any]]:
    """Filter media items by captured_at date."""
    
    matching = []
    
    for item in media_items:
        captured_at = item.get('captured_at')
        
        if not captured_at:
            continue
        
        try:
            captured_date = captured_at[:10]  # YYYY-MM-DD
            
            # Apply filters
            if before_date and captured_date >= before_date:
                continue
            
            if after_date and captured_date < after_date:
                continue
            
            matching.append(item)
            
        except:
            continue
    
    return matching


def display_results(records: List[Dict[str, Any]]):
    """Display filtered results."""
    if not records:
        print("No matching files found.")
        return
    
    print("="*90)
    print(f"{'#':<4} {'Filename':<25} {'Captured':<12} {'Created':<12} {'Size':<10} {'Camera'}")
    print("-"*90)
    
    # Sort by captured_at
    sorted_records = sorted(records, key=lambda x: x.get('captured_at', ''))
    
    for idx, item in enumerate(sorted_records, 1):
        filename = item.get('filename', 'N/A')[:24]
        captured_at = item.get('captured_at', 'N/A')[:10]
        created_at = item.get('created_at', 'N/A')[:10]
        camera = item.get('camera_model', 'N/A')[:15]
        
        file_size = item.get('file_size', 0) or 0
        if filename.lower().endswith(('.jpg', '.jpeg')):
            size_mb = file_size / (1024 ** 2)
            size_str = f"{size_mb:.2f} MB"
        else:
            size_gb = file_size / (1024 ** 3)
            size_str = f"{size_gb:.2f} GB"
        
        print(f"{idx:<4} {filename:<25} {captured_at:<12} {created_at:<12} {size_str:<10} {camera}")
    
    print()
    print("="*90)
    
    # Summary
    total_size = sum(item.get('file_size', 0) or 0 for item in records)
    total_size_gb = total_size / (1024 ** 3)
    
    print(f"\nTotal files: {len(records)}")
    print(f"Total size: {total_size_gb:.2f} GB")
    
    # Group by year
    by_year = {}
    for item in records:
        captured_at = item.get('captured_at', '')
        if captured_at:
            year = captured_at[:4]
            by_year[year] = by_year.get(year, 0) + 1
    
    if by_year:
        print("\nFiles by year:")
        for year in sorted(by_year.keys()):
            print(f"  {year}: {by_year[year]} files")
    
    print()


def main():
    """Main execution."""
    args = parse_args()
    
    if not args.before and not args.after:
        print("Error: Must specify --before and/or --after")
        print("\nExamples:")
        print("  python scripts/query_gopro_by_date.py --before 2020-06-01")
        print("  python scripts/query_gopro_by_date.py --after 2021-01-01")
        return 1
    
    print("\n" + "="*90)
    print(" QUERY GOPRO API BY CAPTURE DATE")
    print("="*90)
    
    # Get credentials
    credentials = get_credentials(args.secret)
    cookies = credentials.get('cookies', '')
    user_agent = credentials.get('user-agent', 'Mozilla/5.0')
    
    # Fetch all media from GoPro API
    all_media = fetch_all_gopro_media(cookies, user_agent)
    
    # Filter by date
    print(f"Filtering by captured_at date...")
    if args.before:
        print(f"  Before: {args.before}")
    if args.after:
        print(f"  After: {args.after}")
    print()
    
    matching = filter_by_date(all_media, args.before, args.after)
    
    # Display results
    display_results(matching)
    
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
