import os
import json
import time
import requests

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Update these to exactly match your Smartsheet column headers.
COLUMN_MAP = {
    "site_id":          "SITE ID",
    "store_name":       "Store Name",
    "deal_type":        "Deal Type",
    "full_address":     "Full Address",
    "street":           "Address",
    "city":             "City",
    "state":            "State",
    "zip":              "Zip",
    "drumline":         "Drumline",
    "gross_sales_rank": "Gross Sales Rank",
    "deal_notes":       "Latest Comment",
}

CACHE_FILE  = "geocode_cache.json"
OUTPUT_FILE = "docs/properties.json"

# ── SMARTSHEET ────────────────────────────────────────────────────────────────

def fetch_sheet():
    token    = os.environ["SMARTSHEET_TOKEN"]
    sheet_id = os.environ["SMARTSHEET_SHEET_ID"]

    resp = requests.get(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def parse_rows(sheet):
    """Return a list of dicts keyed by COLUMN_MAP values."""
    col_id_to_name = {c["id"]: c["title"] for c in sheet["columns"]}
    reverse = {v: k for k, v in COLUMN_MAP.items()}   # header → field key

    rows = []
    for row in sheet.get("rows", []):
        record = {}
        for cell in row.get("cells", []):
            header = col_id_to_name.get(cell.get("columnId"), "")
            field  = reverse.get(header)
            if field:
                record[field] = str(cell.get("value") or "").strip()
        if record.get("site_id"):          # skip rows with no Site ID
            rows.append(record)
    return rows


# ── GEOCODING ─────────────────────────────────────────────────────────────────

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def cache_key(row):
    return "|".join([
        row.get("street", ""),
        row.get("city",   ""),
        row.get("state",  ""),
        row.get("zip",    ""),
    ]).lower()


def geocode_address(street, city, state, zip_code):
    """Census Bureau geocoder — free, no API key required."""
    try:
        resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/address",
            params={
                "street":    street,
                "city":      city,
                "state":     state,
                "zip":       zip_code,
                "benchmark": "2020",
                "format":    "json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        matches = resp.json()["result"]["addressMatches"]
        if matches:
            coords = matches[0]["coordinates"]
            return coords["y"], coords["x"]   # lat, lng
    except Exception as e:
        print(f"    Geocode error ({street}, {city}): {e}")
    return None, None


def enrich_with_coords(rows, cache):
    new_geocodes = 0
    for row in rows:
        key = cache_key(row)
        if key in cache:
            row["lat"], row["lng"] = cache[key]
        else:
            print(f"  Geocoding: {row.get('street')}, {row.get('city')}, {row.get('state')}")
            lat, lng = geocode_address(
                row.get("street", ""),
                row.get("city",   ""),
                row.get("state",  ""),
                row.get("zip",    ""),
            )
            cache[key] = [lat, lng]
            row["lat"], row["lng"] = lat, lng
            new_geocodes += 1
            time.sleep(0.3)   # be polite to the Census API

    print(f"  {new_geocodes} new geocodes, {len(rows) - new_geocodes} from cache.")
    return rows


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching Smartsheet…")
    sheet = fetch_sheet()
    rows  = parse_rows(sheet)
    print(f"  {len(rows)} rows found.")

    print("Geocoding…")
    cache = load_cache()
    rows  = enrich_with_coords(rows, cache)
    save_cache(cache)

    # Drop rows that couldn't be geocoded
    valid = [r for r in rows if r["lat"] and r["lng"]]
    print(f"  {len(valid)} rows with valid coordinates.")

    os.makedirs("docs", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(valid, f, indent=2)
    print(f"Wrote {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
