#!/usr/bin/env python3
"""
Query DynamoDB files by capture date.

This script queries files from DynamoDB based on when they were captured
using the captured_at metadata field.

Usage:
    # Files captured before June 2020:
    python scripts/query_by_capture_date.py --before 2020-06-01
    
    # Files captured after a date:
    python scripts/query_by_capture_date.py --after 2021-01-01
    
    # Files captured between dates:
    python scripts/query_by_capture_date.py --after 2020-01-01 --before 2021-01-01
"""

import argparse
import boto3
import sys
from datetime import datetime
from typing import List, Dict, Any

dynamodb = boto3.resource('dynamodb')


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Query DynamoDB files by capture date'
    )
    parser.add_argument(
        '--table',
        default='gopro-sync-tracker-dev',
        help='DynamoDB table name'
    )
    parser.add_argument(
        '--before',
        help='Show files captured before this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--after',
        help='Show files captured after this date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--output',
        choices=['summary', 'table', 'json'],
        default='table',
        help='Output format (summary, table, json)'
    )
    return parser.parse_args()


def scan_table_with_date_filter(table_name: str, before_date: str = None, after_date: str = None) -> List[Dict[str, Any]]:
    """
    Scan DynamoDB and filter by captured_at date.
    
    Args:
        table_name: DynamoDB table name
        before_date: ISO date string (YYYY-MM-DD)
        after_date: ISO date string (YYYY-MM-DD)
        
    Returns:
        List of matching records
    """
    table = dynamodb.Table(table_name)
    print(f"\nScanning table: {table_name}")
    
    if before_date:
        print(f"Filter: captured BEFORE {before_date}")
    if after_date:
        print(f"Filter: captured AFTER {after_date}")
    
    print()
    
    matching_records = []
    scanned_count = 0
    
    # Scan all records
    response = table.scan()
    
    for item in response['Items']:
        scanned_count += 1
        captured_at = item.get('captured_at')
        
        if not captured_at:
            continue  # Skip items without captured_at
        
        # Parse date
        try:
            captured_date = captured_at[:10]  # YYYY-MM-DD
            
            # Apply filters
            if before_date and captured_date >= before_date:
                continue
            
            if after_date and captured_date < after_date:
                continue
            
            matching_records.append(item)
            
        except Exception as e:
            print(f"  ⚠ Error parsing date for {item.get('media_id')}: {e}")
            continue
    
    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        
        for item in response['Items']:
            scanned_count += 1
            captured_at = item.get('captured_at')
            
            if not captured_at:
                continue
            
            try:
                captured_date = captured_at[:10]
                
                if before_date and captured_date >= before_date:
                    continue
                
                if after_date and captured_date < after_date:
                    continue
                
                matching_records.append(item)
                
            except:
                continue
    
    print(f"Scanned {scanned_count} records, found {len(matching_records)} matches\n")
    return matching_records


def display_summary(records: List[Dict[str, Any]]):
    """Display summary statistics."""
    if not records:
        print("No matching records found.")
        return
    
    total_size = sum(int(r.get('file_size', 0)) for r in records)
    total_size_gb = total_size / (1024 ** 3)
    
    print("="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total files: {len(records)}")
    print(f"Total size: {total_size_gb:.2f} GB")
    
    # Group by year
    by_year = {}
    for r in records:
        captured_at = r.get('captured_at', '')
        if captured_at:
            year = captured_at[:4]
            by_year[year] = by_year.get(year, 0) + 1
    
    if by_year:
        print("\nFiles by year:")
        for year in sorted(by_year.keys()):
            print(f"  {year}: {by_year[year]} files")
    
    print()


def display_table(records: List[Dict[str, Any]]):
    """Display records in table format."""
    if not records:
        print("No matching records found.")
        return
    
    print("="*80)
    print(f"{'#':<4} {'Filename':<25} {'Captured':<12} {'Size':<10} {'S3 Key'}")
    print("-"*80)
    
    # Sort by captured_at
    sorted_records = sorted(records, key=lambda x: x.get('captured_at', ''))
    
    for idx, record in enumerate(sorted_records, 1):
        filename = record.get('filename', 'N/A')[:24]
        captured_at = record.get('captured_at', 'N/A')[:10]
        
        file_size = int(record.get('file_size', 0))
        if filename.lower().endswith(('.jpg', '.jpeg')):
            size_mb = file_size / (1024 ** 2)
            size_str = f"{size_mb:.2f} MB"
        else:
            size_gb = file_size / (1024 ** 3)
            size_str = f"{size_gb:.2f} GB"
        
        s3_key = record.get('s3_key', 'N/A')
        
        print(f"{idx:<4} {filename:<25} {captured_at:<12} {size_str:<10} {s3_key}")
    
    print()


def main():
    """Main execution."""
    args = parse_args()
    
    if not args.before and not args.after:
        print("Error: Must specify --before and/or --after")
        print("\nExamples:")
        print("  python scripts/query_by_capture_date.py --before 2020-06-01")
        print("  python scripts/query_by_capture_date.py --after 2021-01-01")
        print("  python scripts/query_by_capture_date.py --after 2020-01-01 --before 2021-01-01")
        return 1
    
    print("\n" + "="*80)
    print(" QUERY FILES BY CAPTURE DATE")
    print("="*80)
    
    # Query DynamoDB
    records = scan_table_with_date_filter(args.table, args.before, args.after)
    
    # Display results
    if args.output == 'summary':
        display_summary(records)
    elif args.output == 'table':
        display_table(records)
        display_summary(records)
    elif args.output == 'json':
        import json
        print(json.dumps(records, indent=2, default=str))
    
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
