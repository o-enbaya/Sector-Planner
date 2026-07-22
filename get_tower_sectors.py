import requests
import sys
import csv
import os
import re
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# CONFIGURATION
# ==========================================
LNMS_API_TOKEN = "YOUR_LIBRENMS_API_TOKEN"
LNMS_BASE_API_URL = "http://YOUR_LIBRENMS_SERVER_IP:PORT/api/v0"

CNMAESTRO_BASE_URL = "https://YOUR_CNMAESTRO_SERVER_IP/api/v2"
CNMAESTRO_CLIENT_ID = "YOUR_CNMAESTRO_CLIENT_ID"
CNMAESTRO_CLIENT_SECRET = "YOUR_CNMAESTRO_CLIENT_SECRET"

COMPANY_PREFIX = "COMPANY" # Change to your company prefix (e.g. HTI, MYCORP)

HEADERS = {
    'X-Auth-Token': LNMS_API_TOKEN,
    'Accept': 'application/json'
}

TARGET_OS_LIST = ['routeros', 'canopy', 'cambium', 'cambium-ptmp']

# ==========================================
# cnMaestro API FUNCTIONS
# ==========================================
def get_cnmaestro_token():
    url = f"{CNMAESTRO_BASE_URL.replace('/api/v2', '')}/api/v2/access/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": CNMAESTRO_CLIENT_ID,
        "client_secret": CNMAESTRO_CLIENT_SECRET
    }
    res = requests.post(url, data=payload, verify=False, timeout=10)
    res.raise_for_status()
    return res.json().get("access_token")

def fetch_cnmaestro_ap_stats(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    ap_stats = {}
    offset = 0
    while True:
        res = requests.get(f"{CNMAESTRO_BASE_URL}/devices/statistics?offset={offset}&limit=100", headers=headers, verify=False, timeout=20)
        res.raise_for_status()
        items = res.json().get("data", [])
        if not items: break
        
        for s in items:
            if s.get("mode") == "ap":
                ap_name = str(s.get("name")).upper().strip()
                cpe_count = s.get("connected_sms", 0)
                ap_stats[ap_name] = cpe_count
                
        offset += 100
        if len(items) < 100: break
    return ap_stats

def generate_possible_names(sysName, tower_name):
    sysName = str(sysName).upper().strip()
    names = {sysName}
    
    # Handle RF numbers (e.g. COMPANY-SLIM-350-RF60-AS -> COMPANY-SLIM-350-RF-AS)
    rf_clean = re.sub(r'RF\d+', 'RF', sysName)
    names.add(rf_clean)
    
    parts = sysName.split('-')
    rf_clean_parts = rf_clean.split('-')
    
    # Progressively strip suffixes
    for p_list in [parts, rf_clean_parts]:
        for i in range(len(p_list)-1, 1, -1):
            names.add('-'.join(p_list[:i]))
            
    # Also handle removing COMPANY_PREFIX prefix
    prefix_str = f"{COMPANY_PREFIX.upper()}-"
    for n in list(names):
        if n.startswith(prefix_str):
            names.add(n[len(prefix_str):])
            
    # Remove extremely generic names to prevent cross-sector pollution
    bad_names = {COMPANY_PREFIX.upper(), tower_name, f"{COMPANY_PREFIX.upper()}-{tower_name}", "PTP", f"{COMPANY_PREFIX.upper()}-PTP", "RB", f"{COMPANY_PREFIX.upper()}-PTP-{tower_name}"}
    final_names = {n for n in names if n not in bad_names and len(n) > 4}
    
    return sorted(list(final_names), key=len, reverse=True)

def get_sectors_by_location(target_location):
    print(f"[*] Fetching Cambium Device Group (Group 1) from LibreNMS...")
    cambium_device_ids = set()
    try:
        group_response = requests.get(f"{LNMS_BASE_API_URL}/devicegroups/1", headers=HEADERS, timeout=30)
        group_response.raise_for_status()
        for d in group_response.json().get('devices', []):
            if 'device_id' in d:
                cambium_device_ids.add(d['device_id'])
    except requests.exceptions.RequestException as e:
        print(f"[-] ERROR: Failed to fetch Cambium group: {e}")

    print(f"[*] Fetching all devices from LibreNMS...")
    
    try:
        response = requests.get(f"{LNMS_BASE_API_URL}/devices", headers=HEADERS, timeout=30)
        response.raise_for_status()
        devices = response.json().get('devices', [])
    except requests.exceptions.RequestException as e:
        print(f"[-] ERROR: Failed to connect to LibreNMS API: {e}")
        return []

    print(f"[*] Analyzing {len(devices)} devices...")
    
    found_sectors = []
    
    for device in devices:
        loc = str(device.get('location', '')).strip().upper()
        os_type = str(device.get('os', '')).strip().lower()
        dev_id = device.get('device_id')
        
        # Check if this device is at our target location (fuzzy check to catch slight typos)
        if target_location.upper() in loc:
            # Check if it's a Mikrotik (RouterOS) or if it belongs to the Cambium Device Group (Group 1)
            is_mikrotik = (os_type == 'routeros')
            is_cambium = (dev_id in cambium_device_ids)
            
            if is_mikrotik or is_cambium:
                status_str = "UP" if device.get('status') == 1 else "DOWN"
                
                # Determine display OS
                display_os = "ROUTEROS" if is_mikrotik else "CAMBIUM"
                
                found_sectors.append({
                    "hostname": device.get('hostname'),
                    "sysName": device.get('sysName'),
                    "os": display_os,
                    "status": status_str,
                    "location": device.get('location')
                })
                
    # --- COUNT CPEs & EXPORT TO CSV ---
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    csv_input_path = os.path.join(SCRIPT_DIR, "cpe_enriched_subnets.csv")
    csv_output_path = f"{target_location.upper()}_sectors_report.csv"
    
    sector_cpe_counts = {sec['sysName']: 0 for sec in found_sectors}
    
    # Build a master mapping of EVERY possible name variant back to its true sysName
    possible_names_map = {}
    for sec in found_sectors:
        true_name = sec['sysName']
        if not true_name: continue
        variants = generate_possible_names(true_name, target_location.upper())
        for variant in variants:
            # If a variant is somehow generated by two different sectors, the longer original name usually wins
            if variant not in possible_names_map:
                possible_names_map[variant] = true_name
                
    cpes_matched = 0
    if os.path.exists(csv_input_path):
        with open(csv_input_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cpe_site = str(row.get('site_name', '')).strip().upper()
                if cpe_site != target_location.upper():
                    continue
                    
                cpe_sector_raw = str(row.get('sector', '')).strip().upper()
                cpe_ip = row.get('ip')
                
                if not cpe_sector_raw or not cpe_ip or cpe_sector_raw == '-': continue
                
                # O(1) Instant exact match against the generated dictionary of all possible names
                best_match = possible_names_map.get(cpe_sector_raw)
                        
                if best_match:
                    sector_cpe_counts[best_match] += 1
                    cpes_matched += 1
    else:
        print(f"[-] WARNING: CPE data file not found at {csv_input_path}")
        print("    (CPE counts will all be 0)")
        
    # --- FETCH FROM CNMAESTRO ---
    cnm_ap_stats = {}
    if any(sec['os'] == 'CAMBIUM' for sec in found_sectors):
        print("\n[*] Fetching live CPE counts from cnMaestro for Cambium sectors...")
        try:
            token = get_cnmaestro_token()
            cnm_ap_stats = fetch_cnmaestro_ap_stats(token)
            print(f"    -> Successfully pulled {len(cnm_ap_stats)} AP statistics from cnMaestro.")
        except Exception as e:
            print(f"[-] Failed to fetch from cnMaestro: {e}")

    # --- PRINT RESULTS ---
    print("\n" + "="*80)
    print(f"TOWER SECTORS FOUND AT LOCATION: '{target_location}'")
    print("="*80)
    
    if not found_sectors:
        print("[-] No Mikrotik or Cambium devices found at this location.")
        print("    (Tip: Make sure the location is spelled correctly!)")
        return found_sectors
        
    # Sort by OS, then by Hostname
    found_sectors.sort(key=lambda x: (x['os'], x['hostname']))
    
    # Save to CSV and print to terminal simultaneously
    try:
        with open(csv_output_path, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Status", "Vendor (OS)", "IP Address", "Sector Name", "Connected CPEs"])
            
            for sec in found_sectors:
                name = sec['sysName'] if sec['sysName'] else sec['hostname']
                
                cpe_count = 0
                if sec['os'] == 'CAMBIUM':
                    sec_name_upper = str(name).upper().strip()
                    if sec_name_upper in cnm_ap_stats:
                        cpe_count = cnm_ap_stats[sec_name_upper]
                    else:
                        # Since we removed difflib, check if any of the generated variants match what cnMaestro calls it
                        # (Usually cnMaestro AP names are very accurate, but just in case)
                        variants = generate_possible_names(sec_name_upper, target_location.upper())
                        for v in variants:
                            if v in cnm_ap_stats:
                                cpe_count = cnm_ap_stats[v]
                                break
                else:
                    cpe_count = sector_cpe_counts.get(sec['sysName'], 0)
                
                # Print to terminal
                print(f"[{sec['status']}] {sec['os']:<10} | IP: {sec['hostname']:<15} | CPEs: {cpe_count:<3} | Name: {name}")
                
                # Write to CSV
                writer.writerow([sec['status'], sec['os'], sec['hostname'], name, cpe_count])
                
        print("="*80)
        print(f"Total Sectors Found: {len(found_sectors)}")
        print(f"Total CPEs Matched to these Sectors: {cpes_matched}")
        print(f"[*] Successfully saved report to: {csv_output_path}")
    except PermissionError:
        for sec in found_sectors:
            name = sec['sysName'] if sec['sysName'] else sec['hostname']
            print(f"[{sec['status']}] {sec['os']:<10} | IP: {sec['hostname']:<15} | Name: {name}")
        print("="*80)
        print(f"[-] ERROR: Permission denied. Please close {csv_output_path} in Excel to save the CSV report.")
        
    return found_sectors

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = " ".join(sys.argv[1:])
    else:
        target = input("Enter Tower Location (e.g., ARAYAN): ").strip()
        
    if target:
        get_sectors_by_location(target)
    else:
        print("[-] No location provided. Exiting.")
