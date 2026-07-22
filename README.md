# Tower Sector Azimuth & Coverage Planner

This toolset helps network engineers analyze tower sector coverage, identify sector overlaps (interference risk), detect coverage gaps (empty spots with stranded CPEs), map customer density peaks, and plan optimal sector azimuth layouts. 

It integrates with **LibreNMS** and **cnMaestro** APIs to retrieve live sector status, hostname mappings, and connected client counts, using local caching/databases to compute geographic headings for each Customer Premises Equipment (CPE) device.

---

## Important Notice: Security & Placeholders

For security, privacy, and sharing readiness:
1. **API Configurations**: All production IP addresses, domain names, client IDs, secrets, and credentials in the scripts have been replaced with generic placeholders. You must configure them with your own server info and API tokens before running the tool in production.
2. **Demo Database Data**: The local files `towers_cache.json` and `cpe_enriched_subnets.csv` are populated with **mock placeholder data** (`TOWER-A`, `TOWER-B`, and dummy clients) instead of real production coordinates or customer information. Use these mock entries to test the tool, or replace them with your own network dataset.

---

## Features

1. **Live Sector Discovery (`get_tower_sectors.py`)**:
   - Queries LibreNMS for active Mikrotik (RouterOS) and Cambium (Canopy) APs at a specific location.
   - Connects to cnMaestro to query live connected CPE counts for Cambium APs.
   - Maps CPE devices from `cpe_enriched_subnets.csv` to active sectors.
   - Generates a CSV report summarizing the findings: `<LOCATION>_sectors_report.csv`.

2. **Sector & Coverage Planner (`plan_sector_azimuths.py`)**:
   - Calculates compass bearings (0–359°) from the tower to every customer CPE.
   - **Overlaps Analysis**: Spots sectors with overlapping coverage arcs (>5° overlap) representing interference risk.
   - **Gaps Analysis**: Identifies empty coverage arcs and lists "stranded" CPEs residing in those gaps.
   - **Density Mapping**: Visualizes customer density in 30-degree slices via an ASCII bar graph to see where demand is concentrated.
   - **Optimal Boresight Alignment**: Proposes a revised layout using a subset of existing hardware rotated edge-to-edge, aligning their main beams (boresights) with customer peaks.

---

## File Structure

```
Sector Planner/
├── plan_sector_azimuths.py  # Geographic planner & optimization engine
├── get_tower_sectors.py     # Live LibreNMS & cnMaestro API client
├── towers_cache.json        # Mock database of tower names and GPS coordinates
├── cpe_enriched_subnets.csv # Mock database of CPE IP addresses, latitudes, and longitudes
├── requirements.txt         # Python package dependencies
└── README.md                # Project documentation
```

---

## Configuration

Open `get_tower_sectors.py` and replace placeholders with your live API tokens and server endpoints:

```python
# LibreNMS API Configuration
LNMS_API_TOKEN = "YOUR_LIBRENMS_API_TOKEN"
LNMS_BASE_API_URL = "http://YOUR_LIBRENMS_SERVER_IP:PORT/api/v0"

# cnMaestro API Configuration
CNMAESTRO_BASE_URL = "https://YOUR_CNMAESTRO_SERVER_IP/api/v2"
CNMAESTRO_CLIENT_ID = "YOUR_CNMAESTRO_CLIENT_ID"
CNMAESTRO_CLIENT_SECRET = "YOUR_CNMAESTRO_CLIENT_SECRET"

# Prefix matching configuration
COMPANY_PREFIX = "COMPANY" # Change to your company prefix (e.g. HTI, MYCORP)
```

---

## Installation & Setup

1. Clone or download this directory.
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

---

## Usage Instructions (Using Demo Data)

You can run these commands out of the box using the included demo data for `TOWER-A` and `TOWER-B`.

### 1. Discover Active Sectors & Client Counts
Query active sectors and client counts at a given tower location:
```bash
python get_tower_sectors.py TOWER-A
```
*Note: If no arguments are provided, the script will prompt you for the tower location.*

This will:
- Display live sector statuses and CPE counts in the console.
- Output a CSV report named `TOWER-A_sectors_report.csv`.

### 2. Plan Azimuth Layout & Optimize Coverage
Run the coverage analysis and optimal layout planner:
```bash
python plan_sector_azimuths.py TOWER-A
```

#### Advanced Arguments:
- **Filter by Vendor**: Analyze only Cambium or Mikrotik hardware:
  ```bash
  python plan_sector_azimuths.py TOWER-A CAMBIUM
  python plan_sector_azimuths.py TOWER-A MIKROTIK
  ```
- **Exclude Sectors**: Exclude specific sectors from the optimization combinations:
  ```bash
  python plan_sector_azimuths.py TOWER-A --exclude PTP BACKUP
  ```
- **Require Sectors**: Force specific sectors to be included in the proposed combo:
  ```bash
  python plan_sector_azimuths.py TOWER-A --require MAIN-360
  ```
- **JSON Output**: Get layout configurations in raw JSON format (useful for integrations or web frontends):
  ```bash
  python plan_sector_azimuths.py TOWER-A --json
  ```

---

## Data Schema

### `towers_cache.json`
Should contain GPS coordinates for your tower sites:
```json
[
  {
    "name": "TOWER-A",
    "lat": 32.883085,
    "lon": 13.340974
  }
]
```

### `cpe_enriched_subnets.csv`
Must contain CPE profiles with columns:
- `site_name` (e.g., `TOWER-A`)
- `ip` (CPE IP Address)
- `sm_lat` (CPE Latitude)
- `sm_lon` (CPE Longitude)
- `sector` (Assigned Sector Name)

---

*Disclaimer: Internal Network Tools — Use with appropriate credentials.*
