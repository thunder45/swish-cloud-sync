#!/usr/bin/env python3
"""
Debug GoPro API Script

Shows raw API responses to help diagnose video listing issues.
"""

import sys
import os
import json
import boto3
import requests

# Add lambda layer to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lambda_layer/python'))


def main():
    """Main debugging function."""
    print("=" * 70)
    print("  GoPro API Debug Tool")
    print("=" * 70)
    print()
    
    # Retrieve credentials
    print("📥 Retrieving credentials...")
    try:
        secrets_client = boto3.client('secretsmanager')
        response = secrets_client.get_secret_value(SecretId='gopro/credentials')
        credentials = json.loads(response['SecretString'])
        print("✅ Credentials retrieved")
        print()
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)
    
    # Extract auth info
    cookies = credentials.get('cookies', '')
    user_agent = credentials.get('user-agent', 'Mozilla/5.0')
    
    print("🔑 Authentication Info:")
    print(f"   Cookies length: {len(cookies)} characters")
    print(f"   User-Agent: {user_agent[:50]}...")
    print()
    
    # Test different API endpoints and parameters
    test_cases = [
        {
            'name': 'Test 1: Search API - page 1, per_page 10',
            'url': 'https://api.gopro.com/media/search',
            'params': {'page': 1, 'per_page': 10}
        },
        {
            'name': 'Test 2: Search API - page 1, per_page 100',
            'url': 'https://api.gopro.com/media/search',
            'params': {'page': 1, 'per_page': 100}
        },
        {
            'name': 'Test 3: Search API - no params',
            'url': 'https://api.gopro.com/media/search',
            'params': {}
        },
        {
            'name': 'Test 4: Media Library endpoint',
            'url': 'https://api.gopro.com/media',
            'params': {}
        }
    ]
    
    for test in test_cases:
        print("-" * 70)
        print(f"\n🧪 {test['name']}")
        print(f"   URL: {test['url']}")
        print(f"   Params: {test['params']}")
        print()
        
        headers = {
            'Cookie': cookies,
            'User-Agent': user_agent,
            'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://gopro.com/'
        }
        
        try:
            response = requests.get(
                test['url'],
                headers=headers,
                params=test['params'],
                timeout=30
            )
            
            print(f"📊 Response Status: {response.status_code}")
            print(f"📊 Response Headers:")
            for key, value in response.headers.items():
                if key.lower() in ['content-type', 'content-length', 'x-ratelimit-remaining']:
                    print(f"     {key}: {value}")
            print()
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print("📄 Response Body (pretty-printed):")
                    print(json.dumps(data, indent=2, default=str)[:2000])  # First 2000 chars
                    print()
                    
                    # Analyze structure
                    print("🔍 Response Structure Analysis:")
                    print(f"   Type: {type(data)}")
                    if isinstance(data, dict):
                        print(f"   Keys: {list(data.keys())}")
                        if 'media' in data:
                            print(f"   Media items: {len(data['media'])}")
                            if data['media']:
                                print(f"   First item keys: {list(data['media'][0].keys())}")
                        if '_embedded' in data:
                            print(f"   Embedded keys: {list(data['_embedded'].keys())}")
                        if 'total_items' in data:
                            print(f"   Total items: {data['total_items']}")
                        if 'total_pages' in data:
                            print(f"   Total pages: {data['total_pages']}")
                    print()
                    
                except json.JSONDecodeError:
                    print("⚠️  Response is not JSON")
                    print(f"   Body preview: {response.text[:500]}")
                    print()
            else:
                print(f"❌ Request failed")
                print(f"   Body: {response.text[:500]}")
                print()
                
        except Exception as e:
            print(f"❌ Request error: {e}")
            print()
    
    print("=" * 70)
    print()
    print("💡 Tips:")
    print("   • If all tests show 0 videos, your account may be empty")
    print("   • Check https://gopro.com/media-library/ in browser")
    print("   • If you see videos in browser but not here, API structure may differ")
    print("   • Look for keys like 'media', '_embedded', 'items', 'results'")
    print()


if __name__ == '__main__':
    main()
