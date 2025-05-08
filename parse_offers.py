import requests
from bs4 import BeautifulSoup
import json
import sys
from math import radians, sin, cos, sqrt, atan2
from metro_stations import warsaw_metro_stations
import gspread
from google.oauth2.service_account import Credentials
from googlemaps import Client
from datetime import datetime
from dotenv import load_dotenv
import os
import dspy
from dspy.teleprompt import BootstrapFewShot
from dspy.evaluate import Evaluate
import openai

# Load environment variables from .env file
load_dotenv()

# Initialize OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")


# Define DSPy signature for offer analysis
class OfferAnalysis(dspy.Signature):
    """Analyze a real estate offer and extract key information."""

    description: str = dspy.InputField(
        desc="The full description of the real estate offer"
    )
    available_from: str = dspy.OutputField(
        desc="When is the property available for rent? Answer briefly, e.g. 'Immediately', 'From May 1st', 'Not specified'"
    )
    total_monthly_cost: str = dspy.OutputField(
        desc="Total monthly cost (rent + utilities). Answer briefly, e.g. '3500 PLN', 'Not specified'"
    )
    key_advantages: str = dspy.OutputField(
        desc="Maximum 3 key advantages. Answer briefly, e.g. 'Balcony, Parking, New furniture'"
    )


class OfferAnalyzer(dspy.Module):
    def __init__(self):
        super().__init__()
        self.analyzer = dspy.ChainOfThought(
            "description -> available_from, total_monthly_cost, key_advantages"
        )

    def forward(self, description):
        return self.analyzer(description=description)


def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points using Haversine formula"""
    R = 6371  # Earth's radius in kilometers

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c

    return distance


def find_closest_metro_station(lat, lon):
    """Find the closest metro station to given coordinates"""
    closest_station = None
    min_distance = float("inf")

    for station, (station_lat, station_lon) in warsaw_metro_stations.items():
        distance = calculate_distance(lat, lon, station_lat, station_lon)
        if distance < min_distance:
            min_distance = distance
            closest_station = station

    return closest_station


def get_travel_times(gmaps_client, origin_lat, origin_lon, dest_lat, dest_lon):
    """Get travel times using Google Maps Distance Matrix API"""
    try:
        # Get walking directions
        walking_result = gmaps_client.distance_matrix(
            origins=[(origin_lat, origin_lon)],
            destinations=[(dest_lat, dest_lon)],
            mode="walking",
            departure_time=datetime.now(),
        )

        # Get transit directions
        transit_result = gmaps_client.distance_matrix(
            origins=[(origin_lat, origin_lon)],
            destinations=[(dest_lat, dest_lon)],
            mode="transit",
            departure_time=datetime.now(),
        )

        # Extract walking time
        walking_time = (
            walking_result["rows"][0]["elements"][0]["duration"]["text"]
            if walking_result["rows"][0]["elements"][0]["status"] == "OK"
            else "N/A"
        )

        # Extract transit time
        transit_time = (
            transit_result["rows"][0]["elements"][0]["duration"]["text"]
            if transit_result["rows"][0]["elements"][0]["status"] == "OK"
            else "N/A"
        )

        return walking_time, transit_time
    except Exception as e:
        print(f"Error getting travel times: {str(e)}")
        return "N/A", "N/A"


def fetch_offer_details(slug):
    """Fetch offer details from Otodom"""
    url = f"https://www.otodom.pl/pl/oferta/{slug}"
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    }

    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    # Extract data from the script tag
    data = json.loads(soup.find("script", id="__NEXT_DATA__").text.strip())
    return data


def analyze_offer_with_llm(description):
    """Analyze the offer description using DSPy and OpenAI"""
    try:
        # Initialize DSPy with OpenAI
        lm = dspy.LM("openai/gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
        dspy.configure(lm=lm)

        # Create analyzer instance
        analyzer = OfferAnalyzer()

        # Get analysis
        result = analyzer(description=description)

        return {
            "available_from": result.available_from,
            "total_monthly_cost": result.total_monthly_cost,
            "key_advantages": result.key_advantages,
        }
    except Exception as e:
        print(f"Error in LLM analysis: {str(e)}")
        return {
            "available_from": "Error in analysis",
            "total_monthly_cost": "Error in analysis",
            "key_advantages": "Error in analysis",
        }


def should_process_offer(lat, lon):
    """Check if the offer meets the filtering criteria"""
    # Find closest metro station
    closest_station = find_closest_metro_station(lat, lon)
    station_coords = warsaw_metro_stations[closest_station]

    # Calculate distance to closest metro
    distance = calculate_distance(lat, lon, station_coords[0], station_coords[1])

    # Process only if within 1km of metro
    return distance <= 1.0, closest_station, station_coords


def extract_offer_data(data, gmaps_client):
    """Extract relevant data from the parsed JSON"""
    ad = data["props"]["pageProps"]["ad"]

    # Get coordinates
    lat = ad["location"]["coordinates"]["latitude"]
    lon = ad["location"]["coordinates"]["longitude"]

    # Check if offer meets filtering criteria
    should_process, closest_station, station_coords = should_process_offer(lat, lon)

    # Initialize API-related fields with default values
    walking_time = "N/A"
    transit_time = "N/A"
    available_from = "N/A"
    total_monthly_cost = "N/A"
    key_advantages = "N/A"

    # Only invoke APIs if offer meets criteria
    if should_process:
        # Get travel times
        walking_time, transit_time = get_travel_times(
            gmaps_client, station_coords[0], station_coords[1], lat, lon
        )

        # Create comprehensive description for LLM analysis
        description_parts = []

        # Basic information
        description_parts.append(f"Title: {ad['title']}")
        description_parts.append(
            f"Location: {ad['location']['address']['city']['name']}, {ad['location']['address']['district']['name']}"
        )
        description_parts.append(
            f"Address: {ad['location']['address']['street']['name']}"
        )
        description_parts.append(f"Closest Metro: {closest_station}")
        description_parts.append(f"Walking time from metro: {walking_time}")
        description_parts.append(f"Transit time from metro: {transit_time}")

        # Property details
        description_parts.append("\nProperty Details:")
        for char in ad["characteristics"]:
            if char["key"] not in [
                "price",
                "rent",
                "m",
            ]:  # Skip these as they're handled separately
                description_parts.append(f"- {char['label']}: {char['localizedValue']}")

        # Features
        if ad["features"]:
            description_parts.append("\nFeatures:")
            for feature in ad["features"]:
                description_parts.append(f"- {feature}")

        # Additional information
        description_parts.append("\nAdditional Information:")
        description_parts.append(f"- Advertiser Type: {ad['advertiserType']}")
        description_parts.append(f"- Created: {ad['createdAt']}")
        description_parts.append(f"- Modified: {ad['modifiedAt']}")

        # Original description
        description_parts.append("\nDescription:")
        description_parts.append(ad["description"])

        # Combine all parts
        comprehensive_description = "\n".join(description_parts)

        # Analyze description with LLM
        # llm_analysis = analyze_offer_with_llm(comprehensive_description)
        llm_analysis = {
            "available_from": "N/A",
            "total_monthly_cost": "N/A",
            "key_advantages": "N/A",
        }
        available_from = llm_analysis["available_from"]
        total_monthly_cost = llm_analysis["total_monthly_cost"]
        key_advantages = llm_analysis["key_advantages"]
    else:
        print(f"Skipping API calls for offer - too far from metro station")
        comprehensive_description = ad["description"]

    # Get costs
    base_cost = float(ad["characteristics"][0]["value"])  # Price
    rent = float(ad["characteristics"][1]["value"])  # Rent
    total_cost = base_cost + rent  # Total cost

    # Get area
    area = "N/A"
    for feature in ad["characteristics"]:
        if feature["label"] == "Powierzchnia":
            area = feature["value"]
            break

    # Get full URL
    full_url = ad["url"]

    # Get offer ID and slug
    offer_id = ad["id"]
    slug = ad["slug"]

    # Get full address
    address_parts = []
    if ad["location"]["address"]["street"]["name"]:
        address_parts.append(ad["location"]["address"]["street"]["name"])
    if ad["location"]["address"]["district"]["name"]:
        address_parts.append(ad["location"]["address"]["district"]["name"])
    if ad["location"]["address"]["city"]["name"]:
        address_parts.append(ad["location"]["address"]["city"]["name"])
    full_address = ", ".join(address_parts)

    return {
        "closest_metro": closest_station,
        "base_cost": base_cost,
        "total_cost": total_cost,
        "full_url": full_url,
        "area": area,
        "address": full_address,
        "walking_time": walking_time,
        "transit_time": transit_time,
        "description": comprehensive_description,
        "rent": rent,
        "offer_id": offer_id,
        "slug": slug,
        "available_from": available_from,
        "total_monthly_cost": total_monthly_cost,
        "key_advantages": key_advantages,
    }


def save_to_sheets(data, spreadsheet_id, credentials_file):
    """Save data to Google Sheets using gspread"""
    # Authenticate with Google Sheets
    gc = gspread.service_account(filename=credentials_file)

    # Open the spreadsheet
    spreadsheet = gc.open_by_key(spreadsheet_id)

    # Select the first worksheet
    worksheet = spreadsheet.sheet1

    # Determine if offer meets filtering criteria
    meets_criteria = data["walking_time"] != "N/A"
    status = "GREEN" if meets_criteria else "RED"

    # Prepare the row data with rearranged columns
    row_data = [
        status,  # Color indicator column
        data["closest_metro"],
        data["base_cost"],
        data["total_cost"],
        data["full_url"],
        data["area"],
        data["address"],
        data["walking_time"],
        data["transit_time"],
        data["rent"],
        data["offer_id"],
        data["slug"],
        data["available_from"],
        data["total_monthly_cost"],
        data["key_advantages"],
    ]

    # Append the row to the worksheet
    worksheet.append_row(row_data)

    # Apply color formatting to the first column
    last_row = len(worksheet.get_all_values())
    if meets_criteria:
        worksheet.format(
            f"A{last_row}", {"backgroundColor": {"red": 0.0, "green": 1.0, "blue": 0.0}}
        )
    else:
        worksheet.format(
            f"A{last_row}", {"backgroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0}}
        )


def main():
    if len(sys.argv) != 2:
        print("Usage: python otodom_parser.py <offer_slug>")
        sys.exit(1)

    slug = sys.argv[1].strip()
    # Get configuration from environment variables
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    google_maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    credentials_file = "service_account.json"

    if not spreadsheet_id:
        print("Error: SPREADSHEET_ID not found in .env file")
        sys.exit(1)

    if not google_maps_api_key:
        print("Error: GOOGLE_MAPS_API_KEY not found in .env file")
        sys.exit(1)

    try:
        # Initialize Google Maps client
        gmaps_client = Client(key=google_maps_api_key)

        # Fetch and parse offer details
        data = fetch_offer_details(slug)

        # Extract relevant data
        offer_data = extract_offer_data(data, gmaps_client)

        if offer_data is None:
            print(f"Skipping offer {slug} - does not meet filtering criteria")
            sys.exit(0)

        # Save to Google Sheets
        save_to_sheets(offer_data, spreadsheet_id, credentials_file)

        print(f"Successfully processed offer: {slug}")

    except Exception as e:
        print(f"Error processing offer: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
