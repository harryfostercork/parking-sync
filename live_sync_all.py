import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
FEED_URL = "https://data.corkcity.ie/api/3/action/datastore_search"
RESOURCE_ID = "f4677dac-bb30-412e-95a8-d3c22134e3c0"
PLACE_TYPE = "multistorey"  # must be a value your parking_place.place_type allows
SKIP_NAMES = set()  # to leave one out, add its name, e.g. {"Black Ash Park & Ride"}

HEADERS = {"apikey": SECRET_KEY, "Content-Type": "application/json", "Prefer": "return=representation"}


def get_feed():
    response = requests.get(FEED_URL, params={"resource_id": RESOURCE_ID, "limit": 100}, timeout=30)
    response.raise_for_status()
    return response.json()["result"]["records"]


def to_utc(local_text):
    # The council's times are Irish local time with no time zone, so attach Dublin's and convert to UTC
    local = datetime.fromisoformat(local_text).replace(tzinfo=ZoneInfo("Europe/Dublin"))
    return local.astimezone(timezone.utc)


def find_place(name):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/parking_place",
        headers=HEADERS,
        params={"select": "place_id", "name": f"eq.{name}"},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0]["place_id"] if rows else None


def place_details(record):
    return {
        "latitude": record["latitude"],
        "longitude": record["longitude"],
        "total_spaces": record["spaces"],
        "opening_times": record["opening_times"],
        "height_restriction": record["height_restrictions"],
        "price_info": record["price"],
    }


def save_place(record):
    place_id = find_place(record["name"])
    if place_id is None:
        body = {
            "name": record["name"],
            "place_type": PLACE_TYPE,
            "data_source": "Cork City Council open data",
            **place_details(record),
        }
        response = requests.post(f"{SUPABASE_URL}/rest/v1/parking_place", headers=HEADERS, json=body, timeout=30)
        response.raise_for_status()
        return response.json()[0]["place_id"], "added"
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/parking_place",
        headers=HEADERS,
        params={"place_id": f"eq.{place_id}"},
        json=place_details(record),
        timeout=30,
    )
    response.raise_for_status()
    return place_id, "updated"


def already_saved(place_id, source_time):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/live_availability",
        headers=HEADERS,
        params={"select": "source_timestamp", "place_id": f"eq.{place_id}", "order": "captured_at.desc", "limit": 1},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    if not rows or rows[0]["source_timestamp"] is None:
        return False
    return datetime.fromisoformat(rows[0]["source_timestamp"]) == source_time


def save_reading(place_id, record, source_time):
    body = {
        "place_id": place_id,
        "spaces_free": record["free_spaces"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_timestamp": source_time.isoformat(),
    }
    response = requests.post(f"{SUPABASE_URL}/rest/v1/live_availability", headers=HEADERS, json=body, timeout=30)
    response.raise_for_status()


def main():
    for record in get_feed():
        name = record["name"]
        if name in SKIP_NAMES or record["free_spaces"] is None:
            print(f"{name}: skipped")
            continue
        try:
            place_id, place_result = save_place(record)
            source_time = to_utc(record["date"])
            if already_saved(place_id, source_time):
                print(f"{name}: place {place_result}; council reading from {record['date']} already saved")
            else:
                save_reading(place_id, record, source_time)
                print(f"{name}: place {place_result}; saved {record['free_spaces']} free (council time {record['date']})")
        except Exception as error:
            print(f"{name}: FAILED - {error}")


main()