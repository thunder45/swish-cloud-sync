#!/usr/bin/env python3
"""
List GoPro Cloud Videos Script

Simple script to list videos from your GoPro Cloud account.
Useful for testing authentication and seeing what videos are available.
"""

import sys
import os
import json
import boto3
from datetime import datetime

# Add lambda layer to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lambda_layer/python'))

from cloud_sync_common.gopro_provider import GoProProvider


def main():
    """Main function to list GoPro videos."""
    print("=" * 70)
    print("  GoPro Cloud Video Lister")
    print("=" * 70)
    print()
    
    # Retrieve credentials from Secrets Manager
    print("📥 Retrieving credentials from AWS Secrets Manager...")
    try:
        secrets_client = boto3.client('secretsmanager')
        response = secrets_client.get_secret_value(SecretId='gopro/credentials')
        credentials = json.loads(response['SecretString'])
        print("✅ Credentials retrieved successfully")
        print()
    except Exception as e:
        print(f"❌ Failed to retrieve credentials: {e}")
        print()
        print("Make sure you've completed Task 3.3 (creating initial secrets)")
        print("Run: ./scripts/update_gopro_tokens.sh")
        sys.exit(1)
    
    # Check cookie age
    last_updated = credentials.get('last_updated', 'Unknown')
    if last_updated != 'Unknown':
        try:
            updated_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
            current_time = datetime.now(updated_time.tzinfo)
            age_days = (current_time - updated_time).total_seconds() / 86400
            print(f"🕐 Cookie age: {age_days:.1f} days (last updated: {last_updated})")
        except:
            print(f"🕐 Last updated: {last_updated}")
    print()
    
    # Create provider
    print("🔌 Connecting to GoPro Cloud API...")
    provider = GoProProvider()
    
    # Extract auth info
    cookies = credentials.get('cookies', '')
    user_agent = credentials.get('user-agent', 
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
    
    if not cookies:
        print("❌ No cookies found in credentials")
        sys.exit(1)
    
    # List videos
    print("📋 Listing videos from GoPro Cloud...")
    print("   (This may take a few seconds...)")
    print()
    
    try:
        videos = provider.list_media(
            cookies=cookies,
            user_agent=user_agent,
            page_size=100,
            max_results=1000
        )
        
        print(f"✅ Found {len(videos)} videos in your GoPro Cloud library")
        print("=" * 70)
        print()
        
        if not videos:
            print("📭 No videos found in your account")
            print()
            return
        
        # Display videos
        print(f"{'#':<4} {'Media ID':<20} {'Filename':<25} {'Size':<12} {'Duration':<10}")
        print("-" * 70)
        
        for idx, video in enumerate(videos, 1):
            # Format file size
            size_gb = video.file_size / (1024 ** 3)
            size_str = f"{size_gb:.2f} GB"
            
            # Format duration
            duration_min = video.duration / 60
            duration_str = f"{duration_min:.1f} min"
            
            # Truncate filename and media_id if needed
            filename = video.filename[:24]
            media_id = video.media_id[:19]
            
            print(f"{idx:<4} {media_id:<20} {filename:<25} {size_str:<12} {duration_str:<10}")
        
        print()
        print("=" * 70)
        print()
        
        # Summary statistics
        total_size_gb = sum(v.file_size for v in videos) / (1024 ** 3)
        total_duration_min = sum(v.duration for v in videos) / 60
        
        print("📊 Summary:")
        print(f"   Total videos: {len(videos)}")
        print(f"   Total size: {total_size_gb:.2f} GB")
        print(f"   Total duration: {total_duration_min:.1f} minutes ({total_duration_min/60:.1f} hours)")
        print()
        
        # Check for very large libraries
        if len(videos) >= 1000:
            print("⚠️  Note: Your library has 1000+ videos (limit reached)")
            print("   Actual total may be higher - this script shows first 1000")
            print()
        
    except Exception as e:
        print(f"❌ Failed to list videos: {e}")
        print()
        print("Possible causes:")
        print("  • Cookies expired (extract fresh cookies)")
        print("  • Network connectivity issue")
        print("  • GoPro API changed")
        print()
        print("To refresh cookies:")
        print("  ./scripts/update_gopro_tokens.sh")
        sys.exit(1)


if __name__ == '__main__':
    main()
