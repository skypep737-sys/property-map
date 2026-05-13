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
    "deal_status":      "Deal Status",
    "deal_notes":       "Latest Comment",
}

CACHE_FILE    = "geocode_cache.json"
OUTPUT_FILE   = "docs/properties.json"
SURVEY_OUTPUT = "docs/surveys.json"

# Survey sheet column headers — must match your Smartsheet exactly.
SURVEY_COLUMN_MAP = {
    "survey_order":   "Survey Order",
    "rank":           "Rank",
    "street":         "Address",
    "city":           "City",
    "state":          "State",
    "zip":            "Zip",
    "submarket":      "Submarket",
    "available_sqft": "Available SQFT",
    "base_rent":      "Base Rent",
    "opx":            "Opx",
    "site_notes":     "Site Notes",
    "as_built":       "As-Built (former Use)",
    "photo_link":     "Photo Link",
    "flyer_link":     "Flyer Link",
    "broker":         "Broker",
    "broker_email":   "Broker Email",
    "phone":          "Phone",
    "lng":            "Long",
    "lat":            "Lat",
}

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


# ── SURVEYS ───────────────────────────────────────────────────────────────────

def fetch_survey_sheet_by_id(sheet_id):
    token = os.environ["SMARTSHEET_TOKEN"]
    resp = requests.get(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def parse_survey_rows(sheet):
    col_id_to_name = {c["id"]: c["title"] for c in sheet["columns"]}
    reverse = {v: k for k, v in SURVEY_COLUMN_MAP.items()}

    rows = []
    for row in sheet.get("rows", []):
        record = {}
        for cell in row.get("cells", []):
            header = col_id_to_name.get(cell.get("columnId"), "")
            field  = reverse.get(header)
            if field:
                record[field] = str(cell.get("value") or "").strip()
        # Skip rows with no survey order or Red rank
        if not record.get("survey_order"):
            continue
        if record.get("rank", "").strip().lower() == "red":
            print(f"  Skipping Red-ranked site: {record.get('street', '')}")
            continue
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

    # ── Surveys — supports comma-separated sheet IDs in SURVEY_SHEET_ID ─────────
    survey_ids_raw = os.environ.get("SURVEY_SHEET_ID", "").strip()
    if survey_ids_raw:
        survey_ids = [s.strip() for s in survey_ids_raw.split(",") if s.strip()]
        all_surveys_out = {}

        for sid in survey_ids:
            print(f"Fetching survey sheet {sid}…")
            try:
                ssheet = fetch_survey_sheet_by_id(sid)
                sheet_name = ssheet.get("name", f"Survey {sid}")
                print(f"  Sheet name: {sheet_name!r}")
                srows = parse_survey_rows(ssheet)
                print(f"  {len(srows)} rows (Red-ranked excluded).")

                print("  Geocoding…")
                for row in srows:
                    lat = row.get("lat", "").strip()
                    lng = row.get("lng", "").strip()
                    if lat and lng and lat not in ("", "None") and lng not in ("", "None"):
                        continue
                    key = cache_key(row)
                    if key in cache:
                        row["lat"], row["lng"] = cache[key]
                    else:
                        print(f"    Geocoding: {row.get('street')}, {row.get('city')}")
                        glat, glng = geocode_address(
                            row.get("street", ""), row.get("city", ""),
                            row.get("state", ""),  row.get("zip", ""),
                        )
                        cache[key] = [glat, glng]
                        row["lat"], row["lng"] = glat, glng
                        time.sleep(0.3)

                valid = [
                    r for r in srows
                    if r.get("lat") and r.get("lng")
                    and str(r["lat"]) not in ("", "None")
                    and str(r["lng"]) not in ("", "None")
                ]
                print(f"  {len(valid)} sites with valid coordinates.")
                all_surveys_out[sheet_name] = valid

            except Exception as e:
                print(f"  Error fetching sheet {sid}: {e}")

        save_cache(cache)
        with open(SURVEY_OUTPUT, "w") as f:
            json.dump(all_surveys_out, f, indent=2)
        print(f"Wrote {SURVEY_OUTPUT}.")
    else:
        print("SURVEY_SHEET_ID not set — writing empty surveys.json.")
        with open(SURVEY_OUTPUT, "w") as f:
            json.dump({}, f)


if __name__ == "__main__":
    main()
