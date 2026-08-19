#!/usr/bin/env python3
"""
Script to inspect DynamoDB sync tracker table contents.

This helps understand what's stored in DynamoDB and why _2 files
might not have matching records.

Usage:
    python scripts/inspect_dynamodb.py --table gopro-sync-tracker-dev
    
    # Show only records with _2 in s3_key:
    python scripts/inspect_dynamodb.py --table gopro-sync-tracker-dev --filter _2
    
    # Show sample of all records:
    python scripts/inspect_dynamodb.py --table gopro-sync-tracker-dev --limit 20
"""

import argparse
import boto3
import json
from typing import Dict, Any, List

dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Inspect DynamoDB sync tracker table'
    )
    parser.add_argument(
        '--table',
        required=True,
        help='DynamoDB table name (e.g., gopro-sync-tracker-dev)'
    )
    parser.add_argument(
        '--filter',
        help='Filter s3_key by pattern (e.g., _2, _3, GH011189)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=100,
        help='Maximum records to show (default: 100)'
    )
    parser.add_argument(
        '--show-all-fields',
        action='store_true',
        help='Show all fields instead of summary'
    )
    return parser.parse_args()


def scan_table(table_name: str, filter_pattern: str = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Scan DynamoDB table and return items.
    
    Args:
        table_name: DynamoDB table name
        filter_pattern: Optional pattern to filter s3_key
        limit: Maximum items to return
        
    Returns:
        List of items from table
    """
    table = dynamodb.Table(table_name)
    
    print(f"\nScanning table: {table_name}")
    if filter_pattern:
        print(f"Filter: s3_key contains '{filter_pattern}'")
    print(f"Limit: {limit} records\n")
    
    items = []
    scan_kwargs = {'Limit': limit}
    
    if filter_pattern:
        scan_kwargs['FilterExpression'] = 'contains(s3_key, :pattern)'
        scan_kwargs['ExpressionAttributeValues'] = {':pattern': filter_pattern}
    
    try:
        response = table.scan(**scan_kwargs)
        items = response.get('Items', [])
        
        # Handle pagination if needed
        while 'LastEvaluatedKey' in response and len(items) < limit:
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = table.scan(**scan_kwargs)
            items.extend(response.get('Items', []))
            
            if len(items) >= limit:
                items = items[:limit]
                break
        
        print(f"Found {len(items)} records")
        return items
        
    except Exception as e:
        print(f"Error scanning table: {e}")
        return []


def analyze_table_structure(items: List[Dict[str, Any]]) -> None:
    """Analyze and display table structure."""
    if not items:
        print("\nNo items found in table.")
        return
    
    # Collect all unique fields
    all_fields = set()
    for item in items:
        all_fields.update(item.keys())
    
    print("\n" + "="*80)
    print("TABLE STRUCTURE")
    print("="*80)
    print(f"\nTotal records scanned: {len(items)}")
    print(f"\nFields found in table:")
    for field in sorted(all_fields):
        # Count how many records have this field
        count = sum(1 for item in items if field in item)
        percentage = (count / len(items)) * 100
        print(f"  • {field}: {count}/{len(items)} records ({percentage:.1f}%)")


def display_items(items: List[Dict[str, Any]], show_all: bool = False) -> None:
    """Display items in readable format."""
    if not items:
        return
    
    print("\n" + "="*80)
    print("RECORDS")
    print("="*80)
    
    for i, item in enumerate(items, 1):
        print(f"\n{i}. media_id: {item.get('media_id', 'N/A')}")
        
        if show_all:
            # Show all fields
            for key, value in sorted(item.items()):
                if key != 'media_id':
                    print(f"   {key}: {value}")
        else:
            # Show summary fields
            print(f"   status: {item.get('status', 'N/A')}")
            print(f"   filename: {item.get('filename', 'N/A')}")
            print(f"   s3_key: {item.get('s3_key', 'N/A')}")
            print(f"   file_size: {item.get('file_size', 'N/A')}")
            
            # Show new metadata fields if present
            if item.get('captured_at'):
                print(f"   captured_at: {item['captured_at']}")
            if item.get('resolution'):
                print(f"   resolution: {item['resolution']}")


def check_for_duplicate_records(items: List[Dict[str, Any]]) -> None:
    """Check how many records have _2, _3 etc in s3_key."""
    print("\n" + "="*80)
    print("DUPLICATE ANALYSIS")
    print("="*80)
    
    patterns = ['_2', '_3', '_4', '_5']
    
    for pattern in patterns:
        matching = [item for item in items if item.get('s3_key', '').find(pattern) != -1]
        if matching:
            print(f"\nRecords with '{pattern}' in s3_key: {len(matching)}")
            for item in matching[:5]:  # Show first 5
                print(f"  • {item.get('s3_key', 'N/A')}")
            if len(matching) > 5:
                print(f"  ... and {len(matching) - 5} more")
    
    # Check for records missing s3_key
    no_s3_key = [item for item in items if not item.get('s3_key')]
    if no_s3_key:
        print(f"\n⚠ Records missing s3_key: {len(no_s3_key)}")


def main():
    """Main execution."""
    args = parse_args()
    
    print("\n" + "="*80)
    print(" DYNAMODB TABLE INSPECTOR")
    print("="*80)
    
    # Scan table
    items = scan_table(args.table, args.filter, args.limit)
    
    if not items:
        print("\n⚠ No records found. Possible reasons:")
        print("  1. Table is empty")
        print("  2. Filter pattern doesn't match any records")
        print("  3. Table name is incorrect")
        print("\nTry:")
        print(f"  • aws dynamodb describe-table --table-name {args.table}")
        print(f"  • python scripts/inspect_dynamodb.py --table {args.table} --limit 10")
        return 1
    
    # Analyze structure
    analyze_table_structure(items)
    
    # Check for duplicates
    check_for_duplicate_records(items)
    
    # Display items
    display_items(items, args.show_all_fields)
    
    print("\n" + "="*80)
    print("\nTo see more records or different filters:")
    print(f"  python scripts/inspect_dynamodb.py --table {args.table} --limit 200")
    print(f"  python scripts/inspect_dynamodb.py --table {args.table} --filter GH011189")
    print(f"  python scripts/inspect_dynamodb.py --table {args.table} --show-all-fields")
    print()
    
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
