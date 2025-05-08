#!/bin/bash

# Check if CSV file is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <csv_file>"
    exit 1
fi

CSV_FILE=$1

# Check if file exists
if [ ! -f "$CSV_FILE" ]; then
    echo "Error: File $CSV_FILE does not exist"
    exit 1
fi

# Skip header and process each slug
tail -n +2 "$CSV_FILE" | while IFS=, read -r slug; do
    echo "Processing offer: $slug"
    uv run python3 parse_offers.py "$slug"
    
    # Add a small delay between requests to avoid overwhelming the server
    sleep 2
done