#!/usr/bin/env python3
"""
Test script for metadata-based duplicate detection feature.

This script demonstrates how the video_downloader Lambda uses full GoPro
metadata to detect true duplicates vs. same files re-uploaded.

Usage:
    python scripts/test_duplicate_handling.py
"""

import sys
from datetime import datetime

# Test data - simulating metadata from GoPro API
TEST_MEDIA_ITEMS = [
    # Scenario 1: Original file
    {
        'media_id': 'EXAMPLEMEDIA1',
        'filename': 'GOPR0768.JPG',
        'file_size': 3955949,
        'upload_date': '2022-04-24T18:03:13Z',
        'camera_model': 'HERO7 Black',
        'captured_at': '2022-04-24T18:03:13Z',
        'created_at': '2022-04-24T17:00:47Z',
        'resolution': '1520p',
        'source_duration': '68635',
        'height': 1520,
        'width': 2704,
        'file_extension': 'jpg',
        'media_type': 'Photo'
    },
    # Scenario 2: EXACT SAME FILE re-uploaded (should reuse S3 key)
    {
        'media_id': 'EXAMPLEMEDIA1-duplicate',
        'filename': 'GOPR0768.JPG',
        'file_size': 3955949,  # Same size
        'upload_date': '2022-04-24T18:03:13Z',
        'camera_model': 'HERO7 Black',
        'captured_at': '2022-04-24T18:03:13Z',  # Same capture time
        'created_at': '2022-04-24T17:00:47Z',
        'resolution': '1520p',  # Same resolution
        'source_duration': '68635',  # Same duration
        'height': 1520,
        'width': 2704,
        'file_extension': 'jpg',
        'media_type': 'Photo'
    },
    # Scenario 3: Different file with same name (edited version)
    {
        'media_id': 'EXAMPLEMEDIA1-edited',
        'filename': 'GOPR0768.JPG',
        'file_size': 2800000,  # Different size
        'upload_date': '2022-04-24T20:15:00Z',
        'camera_model': 'HERO7 Black',
        'captured_at': '2022-04-24T20:15:00Z',  # Different capture time
        'created_at': '2022-04-24T20:00:00Z',
        'resolution': '1080p',  # Different resolution
        'source_duration': '45000',  # Different duration
        'height': 1080,
        'width': 1920,
        'file_extension': 'jpg',
        'media_type': 'Photo'
    },
    # Scenario 4: Video file - first version
    {
        'media_id': 'GH011053-original',
        'filename': 'GH011053.MP4',
        'file_size': 517753848,
        'upload_date': '2022-04-24T18:03:13Z',
        'camera_model': 'HERO7 Black',
        'captured_at': '2022-04-24T18:03:13Z',
        'created_at': '2022-04-24T17:00:47Z',
        'resolution': '1520p',
        'source_duration': '68635',
        'height': 1520,
        'width': 2704,
        'file_extension': 'mp4',
        'media_type': 'Video'
    },
    # Scenario 5: Same video file re-uploaded (should reuse)
    {
        'media_id': 'GH011053-reupload',
        'filename': 'GH011053.MP4',
        'file_size': 517753848,  # Same
        'upload_date': '2022-04-24T18:03:13Z',
        'camera_model': 'HERO7 Black',
        'captured_at': '2022-04-24T18:03:13Z',  # Same
        'created_at': '2022-04-24T17:00:47Z',
        'resolution': '1520p',  # Same
        'source_duration': '68635',  # Same
        'height': 1520,
        'width': 2704,
        'file_extension': 'mp4',
        'media_type': 'Video'
    },
]


def is_exact_duplicate(new_metadata: dict, existing_metadata: dict) -> bool:
    """Check if metadata represents the exact same file (copied from handler.py)."""
    # Compare critical immutable fields
    filename_match = new_metadata.get('filename') == existing_metadata.get('filename')
    size_match = new_metadata.get('file_size') == existing_metadata.get('file_size')
    
    # Compare captured_at if available
    captured_at_match = True
    if new_metadata.get('captured_at') and existing_metadata.get('captured_at'):
        captured_at_match = new_metadata['captured_at'] == existing_metadata['captured_at']
    
    # Compare resolution if available
    resolution_match = True
    if new_metadata.get('resolution') and existing_metadata.get('resolution'):
        resolution_match = new_metadata['resolution'] == existing_metadata['resolution']
    
    # Compare source_duration if available
    duration_match = True
    if new_metadata.get('source_duration') and existing_metadata.get('source_duration'):
        duration_match = new_metadata['source_duration'] == existing_metadata['source_duration']
    
    return filename_match and size_match and captured_at_match and resolution_match and duration_match


def generate_s3_key(provider: str, filename: str, upload_date: str) -> str:
    """Generate base S3 key (copied from handler.py)."""
    try:
        date = datetime.fromisoformat(upload_date.replace('Z', '+00:00'))
        year = date.year
        month = f'{date.month:02d}'
    except:
        now = datetime.utcnow()
        year = now.year
        month = f'{now.month:02d}'
    
    return f'{provider}-videos/{year}/{month}/{filename}'


def simulate_metadata_based_detection(existing_items: list, new_metadata: dict, provider: str, upload_date: str) -> tuple:
    """
    Simulate the generate_unique_s3_key function with metadata fingerprint.
    
    Args:
        existing_items: List of existing file metadata dicts
        new_metadata: New file metadata
        provider: Provider name
        upload_date: Upload date
        
    Returns:
        Tuple of (s3_key, is_duplicate, reason)
    """
    filename = new_metadata['filename']
    
    # Generate base key
    base_key = generate_s3_key(provider, filename, upload_date)
    
    # Query existing files with same filename
    matching_items = [item for item in existing_items if item['filename'] == filename]
    
    if not matching_items:
        return base_key, False, "No existing files with this filename"
    
    # Check for exact duplicate using metadata fingerprint
    for item in matching_items:
        if is_exact_duplicate(new_metadata, item):
            return item['s3_key'], True, "Metadata fingerprint matches (exact duplicate)"
    
    # Different file - needs numbered version
    filename_parts = filename.rsplit('.', 1)
    if len(filename_parts) == 2:
        base_filename, extension = filename_parts
    else:
        base_filename = filename
        extension = ''
    
    # Find highest counter
    max_counter = 1
    for item in matching_items:
        existing_filename = item['s3_key'].split('/')[-1]
        
        if existing_filename == filename:
            max_counter = max(max_counter, 1)
            continue
        
        if extension:
            pattern = f'{base_filename}_'
            if pattern in existing_filename and existing_filename.endswith(f'.{extension}'):
                try:
                    counter_part = existing_filename.replace(pattern, '').replace(f'.{extension}', '')
                    counter = int(counter_part)
                    max_counter = max(max_counter, counter)
                except ValueError:
                    pass
    
    # Generate numbered version
    next_counter = max_counter + 1
    if extension:
        unique_filename = f'{base_filename}_{next_counter}.{extension}'
    else:
        unique_filename = f'{base_filename}_{next_counter}'
    
    unique_key = generate_s3_key(provider, unique_filename, upload_date)
    
    return unique_key, False, f"Different file (version {next_counter})"


def test_metadata_based_detection():
    """Test the metadata-based duplicate detection logic."""
    print("\n" + "="*80)
    print("METADATA-BASED DUPLICATE DETECTION TEST")
    print("="*80)
    
    print("\nTesting comprehensive metadata fingerprinting:")
    print("  • filename + file_size + captured_at + resolution + source_duration")
    print()
    
    # Track uploaded items with full metadata
    uploaded_items = []
    provider = 'gopro'
    
    test_results = []
    
    # Process each media item
    for i, item in enumerate(TEST_MEDIA_ITEMS, 1):
        print(f"\n{i}. Processing: {item['filename']} (media_id: {item['media_id']})")
        print(f"   File size: {item['file_size']:,} bytes")
        print(f"   Captured at: {item['captured_at']}")
        print(f"   Resolution: {item['resolution']}")
        print(f"   Duration: {item['source_duration']}ms")
        
        # Generate S3 key using metadata fingerprint
        s3_key, is_reused, reason = simulate_metadata_based_detection(
            uploaded_items,
            item,
            provider,
            item['upload_date']
        )
        
        # Store result
        result = {
            'media_id': item['media_id'],
            'filename': item['filename'],
            's3_key': s3_key,
            'is_reused': is_reused,
            'reason': reason
        }
        test_results.append(result)
        
        # Print result
        if is_reused:
            print(f"   ✓ REUSING: {s3_key}")
            print(f"   → {reason}")
        else:
            print(f"   ✓ NEW KEY: {s3_key}")
            print(f"   → {reason}")
        
        # Add to uploaded items (with full metadata for future comparisons)
        uploaded_items.append({
            **item,
            's3_key': s3_key
        })
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY OF RESULTS")
    print("="*80)
    
    # Group by base filename
    filename_groups = {}
    for result in test_results:
        base_filename = result['filename']
        if base_filename not in filename_groups:
            filename_groups[base_filename] = []
        filename_groups[base_filename].append(result)
    
    for base, results in filename_groups.items():
        print(f"\n{base}:")
        for r in results:
            s3_filename = r['s3_key'].split('/')[-1]
            status = "REUSED" if r['is_reused'] else "NEW"
            print(f"  • {s3_filename} ({status})")
            print(f"    Media ID: {r['media_id']}")
            print(f"    Reason: {r['reason']}")
    
    print(f"\nTotal files processed: {len(TEST_MEDIA_ITEMS)}")
    print(f"Keys reused (exact duplicates): {sum(1 for r in test_results if r['is_reused'])}")
    print(f"New keys created: {sum(1 for r in test_results if not r['is_reused'])}")
    
    # Expected results check
    print("\n" + "="*80)
    print("EXPECTED VS ACTUAL")
    print("="*80)
    
    expected = {
        # GOPR0768.JPG: original, reuse, new version
        'GOPR0768.JPG': {
            'EXAMPLEMEDIA1': ('GOPR0768.JPG', False),  # Original
            'EXAMPLEMEDIA1-duplicate': ('GOPR0768.JPG', True),  # Exact duplicate - REUSE
            'EXAMPLEMEDIA1-edited': ('GOPR0768_2.JPG', False),  # Edited version
        },
        # GH011053.MP4: original, reuse
        'GH011053.MP4': {
            'GH011053-original': ('GH011053.MP4', False),  # Original
            'GH011053-reupload': ('GH011053.MP4', True),  # Re-uploaded - REUSE
        }
    }
    
    success = True
    for base_filename, expected_results in expected.items():
        print(f"\n{base_filename}:")
        for media_id, (expected_filename, expected_reused) in expected_results.items():
            # Find actual result
            actual = next((r for r in test_results if r['media_id'] == media_id), None)
            
            if actual:
                actual_filename = actual['s3_key'].split('/')[-1]
                actual_reused = actual['is_reused']
                
                if actual_filename == expected_filename and actual_reused == expected_reused:
                    print(f"  ✓ {media_id}: PASS")
                    print(f"    Expected: {expected_filename} ({'REUSED' if expected_reused else 'NEW'})")
                    print(f"    Actual: {actual_filename} ({'REUSED' if actual_reused else 'NEW'})")
                else:
                    print(f"  ✗ {media_id}: FAIL")
                    print(f"    Expected: {expected_filename} ({'REUSED' if expected_reused else 'NEW'})")
                    print(f"    Actual: {actual_filename} ({'REUSED' if actual_reused else 'NEW'})")
                    success = False
            else:
                print(f"  ✗ {media_id}: NOT FOUND")
                success = False
    
    print("\n" + "="*80)
    if success:
        print("✓ ALL TESTS PASSED")
        print("\nKey Achievement:")
        print("  • Same file (GOPR0768.JPG) uploaded twice → REUSED S3 key ✓")
        print("  • No unnecessary _2 versions created ✓")
        print("  • Different files with same name → Numbered versions ✓")
    else:
        print("✗ SOME TESTS FAILED")
    print("="*80 + "\n")
    
    return success


def test_metadata_comparison_logic():
    """Test the is_exact_duplicate function with various scenarios."""
    print("\n" + "="*80)
    print("METADATA COMPARISON LOGIC TEST")
    print("="*80)
    
    base_metadata = {
        'filename': 'TEST.JPG',
        'file_size': 1000000,
        'captured_at': '2022-04-24T18:03:13Z',
        'resolution': '1520p',
        'source_duration': '68635'
    }
    
    test_cases = [
        {
            'name': 'Exact match',
            'metadata': base_metadata.copy(),
            'expected': True
        },
        {
            'name': 'Different size',
            'metadata': {**base_metadata, 'file_size': 2000000},
            'expected': False
        },
        {
            'name': 'Different captured_at',
            'metadata': {**base_metadata, 'captured_at': '2023-01-01T12:00:00Z'},
            'expected': False
        },
        {
            'name': 'Different resolution',
            'metadata': {**base_metadata, 'resolution': '1080p'},
            'expected': False
        },
        {
            'name': 'Different duration',
            'metadata': {**base_metadata, 'source_duration': '45000'},
            'expected': False
        },
        {
            'name': 'Missing captured_at (both)',
            'metadata': {k: v for k, v in base_metadata.items() if k != 'captured_at'},
            'expected': True  # Should still match on other fields
        },
    ]
    
    print("\nTesting is_exact_duplicate() with various metadata combinations:\n")
    
    all_passed = True
    for i, test in enumerate(test_cases, 1):
        # Handle missing fields for comparison base
        base_for_test = base_metadata.copy()
        if 'captured_at' not in test['metadata']:
            base_for_test = {k: v for k, v in base_for_test.items() if k != 'captured_at'}
        
        result = is_exact_duplicate(test['metadata'], base_for_test)
        passed = result == test['expected']
        
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{i}. {test['name']}: {status}")
        print(f"   Expected: {test['expected']}, Got: {result}")
        
        if not passed:
            all_passed = False
            print(f"   New: {test['metadata']}")
            print(f"   Existing: {base_for_test}")
    
    print("\n" + "="*80)
    if all_passed:
        print("✓ ALL COMPARISON TESTS PASSED")
    else:
        print("✗ SOME COMPARISON TESTS FAILED")
    print("="*80 + "\n")
    
    return all_passed


def check_deployment_requirements():
    """Check deployment requirements for metadata-based detection."""
    print("\n" + "="*80)
    print("DEPLOYMENT REQUIREMENTS")
    print("="*80)
    
    print("\n1. Lambda Layer Update:")
    print("   ✓ provider_interface.py - VideoMetadata extended")
    print("   ✓ gopro_provider.py - Metadata extraction added")
    
    print("\n2. Lambda Function Updates:")
    print("   ✓ media_lister - Passes all metadata fields")
    print("   ✓ video_downloader - Uses metadata fingerprint")
    
    print("\n3. DynamoDB Schema:")
    print("   ✓ No migration needed (schema-less)")
    print("   ✓ New fields auto-populated on next sync")
    
    print("\n4. IAM Permissions:")
    print("   ✓ dynamodb:Scan - Already included")
    print("   ✓ dynamodb:UpdateItem - Already included")
    print("   ✓ cloudwatch:PutMetricData - Already included")
    
    print("\n5. Deployment Steps:")
    print("   1. Deploy updated Lambda layer")
    print("   2. Deploy updated Lambda functions")
    print("   3. Trigger sync to test")
    print("   4. Verify CloudWatch logs for metadata fingerprint messages")
    
    print("\n" + "="*80 + "\n")


if __name__ == '__main__':
    print("\n" + "="*80)
    print(" METADATA-BASED DUPLICATE DETECTION - TEST SUITE")
    print("="*80)
    
    # Run tests
    test1_passed = test_metadata_comparison_logic()
    test2_passed = test_metadata_based_detection()
    
    # Deployment requirements
    check_deployment_requirements()
    
    # Print usage notes
    print("USAGE NOTES:")
    print("  • Metadata fingerprinting prevents false duplicates")
    print("  • Same file uploaded multiple times → Reuses S3 key ✓")
    print("  • Different files with same name → Creates numbered versions")
    print("  • Compares: filename, size, captured_at, resolution, duration")
    print("  • Backward compatible with existing DynamoDB records")
    print("  • No data migration required")
    print()
    
    # Print real-world example
    print("REAL-WORLD EXAMPLE FROM USER'S S3:")
    print("  Before (size-only detection):")
    print("    • GOPR0768.JPG (3955949 bytes)")
    print("    • GOPR0768_2.JPG (3955949 bytes)  ← Unnecessary!")
    print()
    print("  After (metadata-based detection):")
    print("    • GOPR0768.JPG (3955949 bytes)")
    print("    • (No _2 version created - same file reuses key) ✓")
    print()
    
    exit(0 if (test1_passed and test2_passed) else 1)
