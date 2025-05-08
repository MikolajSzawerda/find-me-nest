import requests
import json
import csv
from bs4 import BeautifulSoup
from datetime import datetime
import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_existing_offers():
    """Get list of existing offer IDs from the spreadsheet"""
    try:
        # Get configuration from environment variables
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        credentials_file = "service_account.json"

        if not spreadsheet_id:
            print("Error: SPREADSHEET_ID not found in .env file")
            return set()

        # Authenticate with Google Sheets
        gc = gspread.service_account(filename=credentials_file)

        # Open the spreadsheet
        spreadsheet = gc.open_by_key(spreadsheet_id)

        # Select the first worksheet
        worksheet = spreadsheet.sheet1

        # Get all values from the offer_id column (column K)
        existing_offers = set(worksheet.col_values(11)[1:])  # Skip header row

        return existing_offers

    except Exception as e:
        print(f"Error getting existing offers: {str(e)}")
        return set()


def fetch_offers_list():
    """Fetch list of offers from Otodom"""
    url = "https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/mazowieckie/warszawa/warszawa/warszawa"

    params = {
        "limit": "36",
        "description": "metro",
        "priceMax": "6000",
        "daysSinceCreated": "1",
        "priceMin": "3000",
        "roomsNumber": "[TWO,THREE]",
        "by": "DEFAULT",
        "direction": "DESC",
        "viewType": "listing",
    }

    headers = {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        data = json.loads(soup.find("script", id="__NEXT_DATA__").text.strip())

        # Extract offers from the response using the correct path
        offers = data["props"]["pageProps"]["data"]["searchAds"]["items"]

        # Get existing offers from spreadsheet
        existing_offers = get_existing_offers()

        # Filter out offers that are already in the spreadsheet
        new_offers = []
        for offer in offers:
            if str(offer["id"]) not in existing_offers:
                new_offers.append(offer)

        # Extract slugs from new offers
        slugs = [offer["slug"] for offer in new_offers]

        return slugs

    except Exception as e:
        print(f"Error fetching offers: {str(e)}")
        return []


def save_slugs_to_csv(slugs):
    """Save slugs to a CSV file with timestamp in output directory and as current_offers.csv in current directory"""
    # Create output directory if it doesn't exist
    os.makedirs("output", exist_ok=True)

    # Generate filename with timestamp for output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"output/offers_{timestamp}.csv"
    current_filename = "current_offers.csv"

    try:
        # Save to output directory with timestamp
        with open(output_filename, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["slug"])  # Header
            for slug in slugs:
                writer.writerow([slug])

        # Save to current directory with constant name
        with open(current_filename, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["slug"])  # Header
            for slug in slugs:
                writer.writerow([slug])

        print(
            f"Saved {len(slugs)} new slugs to {output_filename} and {current_filename}"
        )
        return output_filename

    except Exception as e:
        print(f"Error saving to CSV: {str(e)}")
        return None


def main():
    print("Fetching offers from Otodom...")
    slugs = fetch_offers_list()

    if not slugs:
        print("No new offers found or error occurred")
        return

    print(f"Found {len(slugs)} new offers")

    # Save slugs to CSV
    csv_file = save_slugs_to_csv(slugs)

    if csv_file:
        print("\nTo process these offers, run:")
        print(
            f"cat {csv_file} | tail -n +2 | xargs -I {{}} python otodom_parser.py {{}}"
        )
        print("\nOr process them one by one:")
        for slug in slugs:
            print(f"python otodom_parser.py {slug}")


if __name__ == "__main__":
    main()
