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
            max_results=2000
        )
        
        print(f"✅ Found {len(videos)} videos in your GoPro Cloud library")
        print("=" * 70)
        print()
        
        if not videos:
            print("📭 No videos found in your account")
            print()
            return
        
        # Display videos
        print(f"{'#':<4} {'Filename':<25} {'Size':<10} {'Captured':<12} {'Created':<12} {'Duration':<8}")
        print("-" * 80)
        
        for idx, video in enumerate(videos, 1):
            # Format file size (MB for JPG/jpg, GB for videos)
            filename_lower = video.filename.lower()
            if filename_lower.endswith('.jpg') or filename_lower.endswith('.jpeg'):
                size_mb = video.file_size / (1024 ** 2)
                size_str = f"{size_mb:.2f} MB"
            else:
                size_gb = video.file_size / (1024 ** 3)
                size_str = f"{size_gb:.2f} GB"
            
            # Format duration
            duration_min = video.duration / 60
            duration_str = f"{duration_min:.1f} min"
            
            # Format dates
            captured_date = 'N/A'
            if video.captured_at:
                try:
                    dt = datetime.fromisoformat(video.captured_at.replace('Z', '+00:00'))
                    captured_date = dt.strftime('%Y-%m-%d')
                except:
                    captured_date = video.captured_at[:10] if len(video.captured_at) >= 10 else 'N/A'
            
            created_date = 'N/A'
            if video.created_at:
                try:
                    dt = datetime.fromisoformat(video.created_at.replace('Z', '+00:00'))
                    created_date = dt.strftime('%Y-%m-%d')
                except:
                    created_date = video.created_at[:10] if len(video.created_at) >= 10 else 'N/A'
            
            # Truncate filename if needed
            filename = video.filename[:24]
            
            print(f"{idx:<4} {filename:<25} {size_str:<10} {captured_date:<12} {created_date:<12} {duration_str:<8}")
        
        print()
        print("=" * 70)
        print()
        
        # Summary statistics
        total_size_gb = sum(v.file_size for v in videos) / (1024 ** 3)
        total_duration_min = sum(v.duration for v in videos) / 60
        
        # Get current timestamp
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        print("📊 Summary:")
        print(f"   Query date/time: {current_time}")
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
