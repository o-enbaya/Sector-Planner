import sys
import os
import json
import csv
import math
import re

# ==========================================
# CONFIGURATION
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOWERS_JSON_FILE = os.path.join(SCRIPT_DIR, "towers_cache.json")
CPE_CSV_FILE = os.path.join(SCRIPT_DIR, "cpe_enriched_subnets.csv")

# Import live sector fetching logic
import get_tower_sectors

# ==========================================
# TRIGONOMETRY & MATH
# ==========================================
def parse_float(val):
    try: return float(val)
    except: return None

def calculate_initial_compass_bearing(pointA, pointB):
    lat1, lon1 = math.radians(pointA[0]), math.radians(pointA[1])
    lat2, lon2 = math.radians(pointB[0]), math.radians(pointB[1])
    diffLong = lon2 - lon1
    x = math.sin(diffLong) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(diffLong))
    initial_bearing = math.degrees(math.atan2(x, y))
    return (initial_bearing + 360) % 360

def normalize_angle(angle):
    return (angle % 360 + 360) % 360

def in_arc(angle, start, end):
    angle = normalize_angle(angle)
    start = normalize_angle(start)
    end = normalize_angle(end)
    if start <= end:
        return start <= angle <= end
    else: # Crosses 360 (North)
        return angle >= start or angle <= end

def arc_overlap(arc1, arc2):
    overlap = 0
    for deg in range(360):
        if in_arc(deg, arc1[0], arc1[1]) and in_arc(deg, arc2[0], arc2[1]):
            overlap += 1
    return overlap

# ==========================================
# MAIN LOGIC
# ==========================================
def plan_azimuths(target_tower, vendor_filter=None, exclude_list=None, require_list=None, is_json=False, original_stdout=None):
    if exclude_list is None: exclude_list = []
    if require_list is None: require_list = []
    
    target_tower = target_tower.upper().strip()
    
    # 1. Load Tower Coords
    tower_lat = tower_lon = None
    if not os.path.exists(TOWERS_JSON_FILE):
        print(f"[-] ERROR: Cannot find {TOWERS_JSON_FILE}")
        return
        
    with open(TOWERS_JSON_FILE, 'r', encoding='utf-8') as f:
        for t in json.load(f):
            if str(t.get('name', '')).upper() == target_tower:
                tower_lat = parse_float(t.get('lat'))
                tower_lon = parse_float(t.get('lon'))
                break
                
    if tower_lat is None:
        print(f"[-] ERROR: Could not find coordinates for tower '{target_tower}'")
        return

    # 2. Load Existing Sectors dynamically from LibreNMS
    sectors = []
    print("\n[*] Connecting to LibreNMS to fetch live sectors...")
    found_sectors = get_tower_sectors.get_sectors_by_location(target_tower)
    
    for sec in found_sectors:
        os_type = str(sec.get('os', '')).upper()
        if vendor_filter == "MIKROTIK" and os_type != "ROUTEROS": continue
        if vendor_filter == "CAMBIUM" and "CAMBIUM" not in os_type and "CANOPY" not in os_type: continue
        
        name = str(sec.get('sysName', '')).upper()
        if not name: continue
        
        # Extract Azimuth and Beamwidth from name (e.g., COMPANY-TOWER-350-RF60-AS)
        # Look for the pattern: -[Azimuth]-RF[Beamwidth]
        match = re.search(r'-(\d{1,3})-RF(\d{2,3})', name)
        if match:
            az = parse_float(match.group(1))
            bw = parse_float(match.group(2))
        else:
            # Fallback if no RF width is specified (e.g., COMPANY-TOWER-90)
            match_az = re.search(r'-(\d{1,3})(?:-|$)', name)
            if match_az:
                az = parse_float(match_az.group(1))
                bw = 90.0 # Assume standard 90 degrees if unspecified
            else:
                continue
                
        if az is not None and bw is not None:
            if bw >= 20: # Ignore PtP
                
                # Check excludes
                is_excluded = False
                for ex in exclude_list:
                    if ex.upper() in name.upper():
                        is_excluded = True
                        break
                if is_excluded: continue
                
                start_angle = normalize_angle(az - (bw/2))
                end_angle = normalize_angle(az + (bw/2))
                sectors.append({"name": name, "az": az, "bw": bw, "arc": (start_angle, end_angle)})

    # 3. Load CPEs
    cpes = []
    if not os.path.exists(CPE_CSV_FILE):
        print(f"[-] ERROR: Cannot find {CPE_CSV_FILE}")
        return
        
    with open(CPE_CSV_FILE, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if str(row.get('site_name')).upper().strip() == target_tower:
                lat = parse_float(row.get('sm_lat'))
                lon = parse_float(row.get('sm_lon'))
                if lat is not None and lon is not None:
                    bearing = calculate_initial_compass_bearing((tower_lat, tower_lon), (lat, lon))
                    cpes.append({"ip": row.get('ip'), "bearing": bearing, "sector": row.get('sector')})

    # 4. Coverage Analysis (Map every degree 0-359)
    coverage_map = {deg: {"sectors": [], "cpes": 0} for deg in range(360)}
    
    for sec in sectors:
        for deg in range(360):
            if in_arc(deg, sec['arc'][0], sec['arc'][1]):
                coverage_map[deg]["sectors"].append(sec['name'])
                
    for cpe in cpes:
        deg = int(round(cpe['bearing'])) % 360
        coverage_map[deg]["cpes"] += 1

    # Find Overlaps
    overlaps = []
    for i in range(len(sectors)):
        for j in range(i+1, len(sectors)):
            ov = arc_overlap(sectors[i]['arc'], sectors[j]['arc'])
            # Only report significant overlaps (> 5 degrees) to ignore edge-touching
            if ov > 5:
                overlaps.append((sectors[i]['name'], sectors[j]['name'], ov))
                
    # Find Gaps
    gaps = []
    in_gap = False
    start_gap = 0
    for deg in range(361):
        d = deg % 360
        covered = len(coverage_map[d]["sectors"]) > 0
        
        if not covered and not in_gap:
            in_gap = True
            start_gap = d
        elif covered and in_gap:
            in_gap = False
            gaps.append((start_gap, (deg - 1) % 360))
            
    # Combine wrap-around gap at 0 degrees if it crosses North
    if in_gap:
        if gaps and gaps[0][0] == 0:
            gaps[0] = (start_gap, gaps[0][1])
        else:
            gaps.append((start_gap, 359))

    # ==========================================
    # PRINT REPORT
    # ==========================================
    print("\n" + "="*80)
    title = f"SECTOR AZIMUTH & COVERAGE PLANNER: {target_tower}"
    if vendor_filter: title += f" ({vendor_filter} ONLY)"
    print(title)
    print("="*80)
    print(f"Total Existing Sectors Analyzed: {len(sectors)}")
    print(f"Total Customer CPEs Mapped: {len(cpes)}\n")
    
    # OVERLAPS
    print("--- 1. OVERLAPPING SECTORS ---")
    if not overlaps:
        print("  [+] EXCELLENT: No significant overlaps detected!")
    else:
        # Sort overlaps by severity (size of overlap)
        overlaps.sort(key=lambda x: x[2], reverse=True)
        for s1, s2, degs in overlaps:
            print(f"  [!] {s1} & {s2} OVERLAP by {degs} deg (Interference Risk)")
            
    # GAPS
    print("\n--- 2. COVERAGE GAPS (EMPTY SPOTS) ---")
    if not gaps:
        print("  [+] EXCELLENT: Tower has full 360 coverage!")
    else:
        for g_start, g_end in gaps:
            size = (g_end - g_start + 360) % 360 + 1
            # Count CPEs stranded in this gap
            stranded = sum(coverage_map[(g_start + deg) % 360]["cpes"] for deg in range(size))
            status = f"CRITICAL: {stranded} Stranded CPEs!" if stranded > 0 else "0 CPEs in gap"
            print(f"  [-] GAP: {g_start:03d} to {g_end:03d} (Size: {size} deg). {status}")

    # DENSITY MAP
    print("\n--- 3. CUSTOMER GEOGRAPHIC DENSITY (30-Deg Slices) ---")
    print("  Use this to find where your customer demand is heaviest,")
    print("  so you can plan new 30/60/90 degree sectors accordingly.")
    
    # Calculate density in 30-degree slices
    max_cpes_in_slice = 0
    slices = []
    for window in range(0, 360, 30):
        w_start = window
        w_end = window + 29
        count = sum(coverage_map[d]["cpes"] for d in range(w_start, w_end + 1))
        slices.append((w_start, w_end, count))
        if count > max_cpes_in_slice: max_cpes_in_slice = count
        
    for w_start, w_end, count in slices:
        # Draw a little ASCII bar graph relative to the densest slice
        bar = "#" * int((count / max(1, max_cpes_in_slice)) * 40) if count > 0 else ""
        print(f"  {w_start:03d} to {w_end:03d} | CPEs: {count:<3} | {bar}")

    # ==========================================
    # SUGGEST OPTIMAL LAYOUT (RE-MAP EXISTING SECTORS)
    # ==========================================
    print("\n--- 4. SUGGESTED OPTIMAL LAYOUT (RE-MAPPING EXISTING SECTORS) ---")
    print("  Calculated by selecting a subset of your EXISTING hardware that sums")
    print("  to ~360°, and rotating them edge-to-edge to perfectly align the")
    print("  boresights with your customer density peaks.\n")
    
    if not sectors:
        print("  [-] No existing sectors to re-map.")
    else:
        # 1. Find a subset of existing sectors that sums to 360 (or as close as possible)
        import itertools
        best_combo = None
        best_diff = 9999
        
        for r in range(1, len(sectors) + 1):
            for combo in itertools.combinations(sectors, r):
                # Check requirements
                missing_req = False
                for req in require_list:
                    if not any(req.upper() in s['name'].upper() for s in combo):
                        missing_req = True
                        break
                if missing_req: continue
                
                total_bw = sum(s['bw'] for s in combo)
                diff = abs(360 - total_bw)
                if diff < best_diff:
                    best_diff = diff
                    best_combo = list(combo)
                    if diff == 0: break
            if best_diff == 0: break
            
        if not best_combo:
            print("  [-] Could not calculate a subset.")
        else:
            total_bw = sum(s['bw'] for s in best_combo)
            print(f"  [*] Selected {len(best_combo)} existing sectors to achieve {total_bw}° coverage.")
            
            # 2. Sort them by beamwidth so we have a consistent edge-to-edge order
            best_combo = sorted(best_combo, key=lambda x: x['bw'], reverse=True)
            
            # 3. Find the optimal rotation to align boresights with CPE density
            best_rotation = 0
            best_score = -999999
            
            for offset in range(360):
                score = 0
                current_angle = offset
                centers = []
                for sec in best_combo:
                    centers.append((current_angle + sec['bw']/2.0) % 360)
                    current_angle += sec['bw']
                    
                for cpe in cpes:
                    cpe_deg = int(round(cpe['bearing'])) % 360
                    min_dist = 360
                    for center in centers:
                        dist = min(abs(cpe_deg - center), 360 - abs(cpe_deg - center))
                        if dist < min_dist: min_dist = dist
                    score += (180.0 - min_dist)
                    
                if score > best_score:
                    best_score = score
                    best_rotation = offset
                    
            # 4. Print the final plan
            final_json_layout = []
            
            print(f"  [*] BEST EDGE-TO-EDGE LAYOUT (Starting at {best_rotation:03d}°)")
            
            current_angle = best_rotation
            unused_sectors = [s for s in sectors if s not in best_combo]
            
            for sec in best_combo:
                center = (current_angle + sec['bw']/2.0) % 360
                start = current_angle % 360
                end = (current_angle + sec['bw']) % 360
                
                cpe_count = 0
                for cpe in cpes:
                    if in_arc(cpe['bearing'], start, end): cpe_count += 1
                
                print(f"      Move '{sec['name']}' ({sec['bw']}°) -> NEW AZIMUTH: {int(center):03d}° (Covers {int(start):03d}° to {int(end):03d}°) | CPEs: {cpe_count}")
                
                final_json_layout.append({
                    "name": sec['name'],
                    "beamwidth": sec['bw'],
                    "azimuth": int(center),
                    "start_arc": int(start),
                    "end_arc": int(end),
                    "cpe_count": cpe_count
                })
                
                current_angle += sec['bw']
                
            if unused_sectors:
                print("\n  [!] SPARE / EXTRA SECTORS (Not needed for 360° coverage):")
                for sec in unused_sectors:
                    print(f"      - {sec['name']} ({sec['bw']}°) -> Recommend decommissioning or using for Layer 2.")
                    
            if is_json and original_stdout:
                sys.stdout = original_stdout
                print(json.dumps({"status": "success", "tower": target_tower, "layout": final_json_layout}))
                sys.exit(0)
                
        print("")
        
    print("="*80)
    
    if is_json and original_stdout:
        sys.stdout = original_stdout
        print(json.dumps({"status": "error", "message": "Could not calculate layout."}))
        sys.exit(1)

import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Azimuth Planner")
    parser.add_argument("target", nargs='?', default="")
    parser.add_argument("vendor", nargs='?', default=None)
    parser.add_argument("--exclude", nargs='*', default=[], help="Exclude sectors containing these strings")
    parser.add_argument("--require", nargs='*', default=[], help="Require sectors containing these strings")
    parser.add_argument("--json", action="store_true", help="Output only raw JSON")
    
    args = parser.parse_args()
    
    original_stdout = None
    if args.json:
        original_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
    
    if not args.target and not args.json:
        args.target = input("Enter Tower Location (e.g., SLIM): ").strip()
        v_input = input("Filter by vendor? (Leave blank for ALL, or type CAMBIUM / MIKROTIK): ").strip().upper()
        if v_input: args.vendor = v_input
        
    if args.target:
        plan_azimuths(args.target, args.vendor, args.exclude, args.require, args.json, original_stdout)
    elif args.json:
        sys.stdout = original_stdout
        import json
        print(json.dumps({"status": "error", "message": "Missing target tower."}))
