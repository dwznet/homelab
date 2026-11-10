#!/usr/bin/env python3
"""
SMART disk health HTTP service — port 9502.
Zwraca JSON z health każdego dysku wykrytego przez smartctl.
"""
import json, subprocess, re, time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9502
CACHE_TTL = 180  # odśwież SMART dane co 3 minuty

_cache = {"data": None, "ts": 0}

# Progi alertów
THRESHOLDS = {
    # (raw_warn, raw_crit)   None = ignoruj
    5:   (10,   50,   "Reallocated_Sector_Ct"),
    187: (0,    10,   "Reported_Uncorrect"),
    196: (100,  500,  "Reallocated_Event_Count"),
    197: (1,    10,   "Current_Pending_Sector"),
    198: (1,    5,    "Offline_Uncorrectable"),
    199: (20,   100,  "UDMA_CRC_Error_Count"),
    # Wear Leveling dla SSD: normalised value spada 100 → 0
    # wartość poniżej 20 = warning, < 10 = critical
    177: None,  # obsługiwane osobno (SSD endurance)
    231: None,  # obsługiwane osobno (SSD life left)
}
TEMP_WARN  = 50
TEMP_CRIT  = 60
WEAR_WARN  = 20   # < 20 normalised = warning
WEAR_CRIT  = 10   # < 10 normalised = critical

def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr
    except Exception as e:
        return str(e)

def scan_disks():
    """Zwróć listę dysków widocznych przez smartctl."""
    out = run(["smartctl", "--scan"])
    disks = []
    for line in out.splitlines():
        m = re.match(r"^(/dev/\S+)", line)
        if m:
            disks.append(m.group(1))
    return disks or ["/dev/sda"]

def parse_smart(disk):
    """Pełna analiza SMART dla jednego dysku."""
    out_a = run(["smartctl", "-A", "-i", "-H", disk])
    result = {
        "disk":        disk,
        "model":       "unknown",
        "serial":      "unknown",
        "type":        "unknown",   # SSD / HDD / NVMe
        "capacity_gb": 0,
        "power_on_h":  0,
        "temp_c":      None,
        "smart_ok":    True,
        "wear_pct":    None,       # % żywotności pozostałej (SSD)
        "reallocated": 0,
        "pending":     0,
        "uncorrectable": 0,
        "crc_errors":  0,
        "reallocated_events": 0,
        "health":      "ok",       # ok / warning / critical
        "alerts":      [],
        "raw_attrs":   {},
    }

    lines = out_a.splitlines()

    # Nagłówek
    for l in lines:
        if "Device Model:" in l or "Product:" in l:
            result["model"] = l.split(":", 1)[-1].strip()
        elif "Serial Number:" in l or "Serial number:" in l:
            result["serial"] = l.split(":", 1)[-1].strip()
        elif "Rotation Rate:" in l:
            rr = l.split(":", 1)[-1].strip()
            result["type"] = "HDD" if "rpm" in rr.lower() else "SSD"
        elif "User Capacity:" in l or "Total NVM Capacity:" in l:
            m = re.search(r"([\d,]+)\s+bytes", l)
            if m:
                result["capacity_gb"] = round(int(m.group(1).replace(",","").replace(".","")) / 1e9, 0)
        elif "SMART overall-health" in l:
            result["smart_ok"] = "PASSED" in l or "OK" in l
        elif "NVMe" in l and result["type"] == "unknown":
            result["type"] = "NVMe"

    if result["type"] == "unknown":
        result["type"] = "SSD"  # brak RPM = SSD

    # Atrybuty ATA
    in_attrs = False
    for l in lines:
        if "ID#" in l and "ATTRIBUTE_NAME" in l:
            in_attrs = True
            continue
        if not in_attrs:
            continue
        parts = l.split()
        if len(parts) < 10:
            continue
        try:
            attr_id   = int(parts[0])
            norm_val  = int(parts[3])
            raw_val   = int(parts[9].split()[0])
        except (ValueError, IndexError):
            continue

        result["raw_attrs"][attr_id] = {"norm": norm_val, "raw": raw_val, "name": parts[1]}

        # Temperatura
        if attr_id in (190, 194):
            result["temp_c"] = raw_val & 0xFF  # niskobajt

        # Power-on hours
        if attr_id == 9:
            result["power_on_h"] = raw_val

        # Wear leveling (SSD)
        if attr_id in (177, 231, 233):
            result["wear_pct"] = norm_val  # normalised 100 = nowy, 0 = zużyty

        # Konkretne wartości
        if attr_id == 5:
            result["reallocated"] = raw_val
        elif attr_id == 197:
            result["pending"] = raw_val
        elif attr_id == 198:
            result["uncorrectable"] = raw_val
        elif attr_id == 199:
            result["crc_errors"] = raw_val
        elif attr_id == 196:
            result["reallocated_events"] = raw_val
        elif attr_id == 187:
            if raw_val > 0:
                result["uncorrectable"] = max(result["uncorrectable"], raw_val)

    # NVMe temperature
    for l in lines:
        m = re.search(r"Temperature:\s+(\d+)\s+Celsius", l)
        if m:
            result["temp_c"] = int(m.group(1))
        m2 = re.search(r"Data Units Written.*?(\d[\d\s,]+)\[", l)
        if m2:
            pass  # można dodać zapisane TB

    # ── Ocena zdrowia ────────────────────────────────────────────────────────
    def add_alert(level, msg):
        result["alerts"].append({"level": level, "msg": msg})
        if level == "critical" and result["health"] != "critical":
            result["health"] = "critical"
        elif level == "warning" and result["health"] == "ok":
            result["health"] = "warning"

    if not result["smart_ok"]:
        add_alert("critical", "SMART OVERALL: FAILED")

    # Reallocated sectors
    if result["reallocated"] >= 50:
        add_alert("critical", f"Reallocated sectors: {result['reallocated']}")
    elif result["reallocated"] >= 10:
        add_alert("warning", f"Reallocated sectors: {result['reallocated']}")

    # Reallocated events (ważniejsze od raw sectors dla SSD)
    if result["reallocated_events"] >= 500:
        add_alert("critical", f"Reallocated events: {result['reallocated_events']}")
    elif result["reallocated_events"] >= 100:
        add_alert("warning", f"Reallocated events: {result['reallocated_events']}")

    # Pending sectors — te mogą być każde
    if result["pending"] >= 10:
        add_alert("critical", f"Pending sectors: {result['pending']}")
    elif result["pending"] >= 1:
        add_alert("warning", f"Pending sectors: {result['pending']}")

    # Uncorrectable
    if result["uncorrectable"] >= 5:
        add_alert("critical", f"Uncorrectable errors: {result['uncorrectable']}")
    elif result["uncorrectable"] >= 1:
        add_alert("warning", f"Uncorrectable errors: {result['uncorrectable']}")

    # CRC errors (kabel SATA)
    if result["crc_errors"] >= 100:
        add_alert("critical", f"CRC/cable errors: {result['crc_errors']}")
    elif result["crc_errors"] >= 20:
        add_alert("warning", f"CRC/cable errors: {result['crc_errors']} (sprawdz kabel!)")

    # Temperatura
    if result["temp_c"] is not None:
        if result["temp_c"] >= TEMP_CRIT:
            add_alert("critical", f"Temperatura: {result['temp_c']}°C")
        elif result["temp_c"] >= TEMP_WARN:
            add_alert("warning", f"Temperatura: {result['temp_c']}°C")

    # Wear leveling SSD
    if result["wear_pct"] is not None:
        if result["wear_pct"] <= WEAR_CRIT:
            add_alert("critical", f"Zuzycie NAND: {result['wear_pct']}% pozostalo")
        elif result["wear_pct"] <= WEAR_WARN:
            add_alert("warning", f"Zuzycie NAND: {result['wear_pct']}% pozostalo")

    return result

def collect_all():
    disks = scan_disks()
    results = []
    overall = "ok"
    for d in disks:
        dr = parse_smart(d)
        results.append(dr)
        if dr["health"] == "critical":
            overall = "critical"
        elif dr["health"] == "warning" and overall == "ok":
            overall = "warning"
    return {
        "disks":     results,
        "health":    overall,
        "count":     len(results),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

def get_data():
    global _cache
    if _cache["data"] is None or (time.time() - _cache["ts"]) > CACHE_TTL:
        _cache["data"] = collect_all()
        _cache["ts"] = time.time()
    return _cache["data"]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # cichy
    def do_GET(self):
        data = get_data()
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    # Wstępne załadowanie
    get_data()
    print(f"Disk health service on port {PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
