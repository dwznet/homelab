#!/usr/bin/env python3
"""CPU Temperature Monitor — Serves thermal zone + hwmon data via HTTP."""
import json, os, glob
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 9501

def read_temps():
    """Read CPU temperatures from thermal_zones and hwmon."""
    zones = []
    cpu_temps = []  # list of per-package/per-die temps

    # 1. Read thermal_zones
    thermal_dir = '/sys/class/thermal/'
    if os.path.isdir(thermal_dir):
        for zone in sorted(os.listdir(thermal_dir)):
            if zone.startswith('thermal_zone'):
                try:
                    with open(f'{thermal_dir}{zone}/temp') as f:
                        temp = int(f.read().strip()) / 1000
                    with open(f'{thermal_dir}{zone}/type') as f:
                        zone_type = f.read().strip()
                    zones.append({'zone': zone, 'type': zone_type, 'temp': temp})
                except Exception:
                    pass

    # Collect pkg temps from thermal_zones (Intel dual-socket)
    pkg_temps = [z['temp'] for z in zones if 'x86_pkg' in z.get('type', '').lower()]
    if pkg_temps:
        for i, t in enumerate(pkg_temps):
            cpu_temps.append({'label': f'CPU{i}', 'temp': round(t, 1)})

    # 2. Read hwmon (fallback and AMD support)
    hwmon_dir = '/sys/class/hwmon/'
    if os.path.isdir(hwmon_dir):
        for hw in sorted(os.listdir(hwmon_dir)):
            hwpath = f'{hwmon_dir}{hw}'
            try:
                with open(f'{hwpath}/name') as f:
                    name = f.read().strip()
            except Exception:
                continue

            # k10temp = AMD CPU, coretemp = Intel CPU
            if name in ('k10temp', 'coretemp'):
                # temp1_input is usually Tctl/Package temp
                try:
                    with open(f'{hwpath}/temp1_input') as f:
                        pkg = int(f.read().strip()) / 1000
                except Exception:
                    pkg = None

                if name == 'k10temp' and pkg is not None:
                    # AMD: Tctl is temp1, Tccd dies are temp3+
                    tccd = []
                    for tf in sorted(glob.glob(f'{hwpath}/temp*_input')):
                        fname = os.path.basename(tf)
                        idx = int(fname.replace('temp','').replace('_input',''))
                        if idx >= 3:  # Tccd dies
                            try:
                                with open(tf) as f:
                                    tccd.append(int(f.read().strip()) / 1000)
                            except Exception:
                                pass
                    if not cpu_temps:  # Don't overwrite if thermal_zone already found
                        cpu_temps.append({'label': 'Tctl', 'temp': round(pkg, 1)})
                        for j, t in enumerate(tccd):
                            cpu_temps.append({'label': f'CCD{j}', 'temp': round(t, 1)})

                elif name == 'coretemp' and pkg is not None and not cpu_temps:
                    # Intel: count coretemp instances for multi-socket
                    cpu_temps.append({'label': f'CPU{len(cpu_temps)}', 'temp': round(pkg, 1)})

    # Single summary temp (max of all package temps)
    cpu_temp = None
    if cpu_temps:
        cpu_temp = round(max(t['temp'] for t in cpu_temps), 1)
    elif zones:
        cpu_temp = round(max(z['temp'] for z in zones), 1)

    return {
        'cpu_temp': cpu_temp,
        'cpu_temps': cpu_temps,
        'zones': zones,
    }

class TempHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps(read_temps())
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data.encode())
    def log_message(self, *args):
        pass

if __name__ == '__main__':
    server = ThreadingHTTPServer(('', PORT), TempHandler)
    server.daemon_threads = True
    print(f'CPU temp monitor on port {PORT}')
    server.serve_forever()
