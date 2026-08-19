#!/bin/bash
"""
Cleanup script for false duplicate files in S3 using AWS CLI.

This script identifies and optionally deletes files with _2, _3 suffixes.

Usage:
    # List all _2 files (dry run):
    ./scripts/cleanup_duplicates.sh list
    
    # Delete all _2 files (requires confirmation):
    ./scripts/cleanup_duplicates.sh delete
    
    # Delete specific pattern (_3, _4, etc.):
    ./scripts/cleanup_duplicates.sh delete _3
"""

# Configuration - UPDATE THESE VALUES
BUCKET_NAME="${BUCKET_NAME:-swish-cloud-sync-gopro-videos}"
PATTERN="${2:-_2}"  # Default to _2, can override with second argument

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to list duplicate files
list_duplicates() {
    echo "================================================================================"
    echo " LISTING DUPLICATE FILES IN S3"
    echo "================================================================================"
    echo ""
    echo "Bucket: $BUCKET_NAME"
    echo "Pattern: Files containing '$PATTERN'"
    echo ""
    
    # List all objects, filter for pattern
    echo "Scanning S3 bucket..."
    aws s3 ls "s3://${BUCKET_NAME}/" --recursive | grep "${PATTERN}\." > /tmp/duplicates.txt
    
    if [ ! -s /tmp/duplicates.txt ]; then
        echo -e "${GREEN}✓ No files found with pattern '${PATTERN}'${NC}"
        rm -f /tmp/duplicates.txt
        return 0
    fi
    
    # Count and calculate size
    COUNT=$(wc -l < /tmp/duplicates.txt | tr -d ' ')
    TOTAL_SIZE=$(awk '{sum+=$3} END {print sum}' /tmp/duplicates.txt)
    TOTAL_SIZE_GB=$(echo "scale=2; $TOTAL_SIZE / 1073741824" | bc)
    
    echo -e "${YELLOW}Found $COUNT files matching pattern '${PATTERN}'${NC}"
    echo "Total size: ${TOTAL_SIZE_GB} GB"
    echo ""
    echo "Files to delete:"
    echo "--------------------------------------------------------------------------------"
    
    # Show files with size
    cat /tmp/duplicates.txt | while read -r line; do
        size=$(echo "$line" | awk '{print $3}')
        file=$(echo "$line" | awk '{print $4}')
        size_mb=$(echo "scale=2; $size / 1048576" | bc)
        echo "  • $file (${size_mb} MB)"
    done
    
    echo ""
    echo "To delete these files, run:"
    echo "  ./scripts/cleanup_duplicates.sh delete"
    echo ""
    
    rm -f /tmp/duplicates.txt
}

# Function to delete duplicate files
delete_duplicates() {
    echo "================================================================================"
    echo " DELETING DUPLICATE FILES FROM S3"
    echo "================================================================================"
    echo ""
    
    # List files first
    aws s3 ls "s3://${BUCKET_NAME}/" --recursive | grep "${PATTERN}\." > /tmp/duplicates.txt
    
    if [ ! -s /tmp/duplicates.txt ]; then
        echo -e "${GREEN}✓ No files found with pattern '${PATTERN}'${NC}"
        rm -f /tmp/duplicates.txt
        return 0
    fi
    
    COUNT=$(wc -l < /tmp/duplicates.txt | tr -d ' ')
    TOTAL_SIZE=$(awk '{sum+=$3} END {print sum}' /tmp/duplicates.txt)
    TOTAL_SIZE_GB=$(echo "scale=2; $TOTAL_SIZE / 1073741824" | bc)
    
    echo -e "${YELLOW}Found $COUNT files to delete${NC}"
    echo "Total size: ${TOTAL_SIZE_GB} GB"
    echo ""
    
    # Show first 10 files as sample
    echo "Sample files (first 10):"
    head -10 /tmp/duplicates.txt | while read -r line; do
        file=$(echo "$line" | awk '{print $4}')
        echo "  • $file"
    done
    
    if [ $COUNT -gt 10 ]; then
        echo "  ... and $((COUNT - 10)) more"
    fi
    
    echo ""
    echo -e "${RED}⚠  WARNING: This will permanently delete $COUNT files (${TOTAL_SIZE_GB} GB)${NC}"
    echo -e "${RED}⚠  This action CANNOT be undone!${NC}"
    echo ""
    read -p "Type 'DELETE' to confirm: " confirmation
    
    if [ "$confirmation" != "DELETE" ]; then
        echo ""
        echo "Cancelled by user."
        rm -f /tmp/duplicates.txt
        return 1
    fi
    
    echo ""
    echo "Deleting files..."
    
    # Create list of files to delete
    awk '{print $4}' /tmp/duplicates.txt > /tmp/delete_list.txt
    
    # Delete files in batches
    deleted=0
    failed=0
    
    while IFS= read -r file; do
        if aws s3 rm "s3://${BUCKET_NAME}/${file}" 2>/dev/null; then
            ((deleted++))
            echo -e "${GREEN}✓${NC} Deleted ($deleted/$COUNT): $file"
        else
            ((failed++))
            echo -e "${RED}✗${NC} Failed ($failed): $file"
        fi
    done < /tmp/delete_list.txt
    
    echo ""
    echo "================================================================================"
    echo " CLEANUP COMPLETE"
    echo "================================================================================"
    echo "Deleted: $deleted files"
    
    if [ $failed -gt 0 ]; then
        echo -e "${RED}Failed: $failed files${NC}"
    fi
    
    echo "Storage freed: ~${TOTAL_SIZE_GB} GB"
    echo ""
    
    # Cleanup temp files
    rm -f /tmp/duplicates.txt /tmp/delete_list.txt
}

# Main script
case "$1" in
    list)
        list_duplicates
        ;;
    delete)
        delete_duplicates
        ;;
    *)
        echo "Usage: $0 {list|delete} [pattern]"
        echo ""
        echo "Examples:"
        echo "  $0 list           # List all _2 files"
        echo "  $0 list _3        # List all _3 files"
        echo "  $0 delete         # Delete all _2 files (with confirmation)"
        echo "  $0 delete _3      # Delete all _3 files"
        echo ""
        echo "Set BUCKET_NAME environment variable to override default bucket."
        exit 1
        ;;
esac
