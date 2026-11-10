#!/usr/bin/env python3
"""AMD iGPU monitor — reads gpu_busy_percent, frequency, temp, power from sysfs.
Serves JSON on port 9503 (same schema as Intel igpu-monitor for dashboard compat).
"""
import http.server, json, threading, time, os

PORT = 9503
GPU_BUSY = "/sys/class/drm/card0/device/gpu_busy_percent"
GPU_SCLK = "/sys/class/drm/card0/device/pp_dpm_sclk"
HWMON_DIR = None  # auto-detect

_data = {"gpu_pct": 0, "render_pct": 0, "video_pct": 0,
         "freq_mhz": 0, "freq_req": 0, "rc6_pct": 0,
         "power_gpu": 0, "power_pkg": 0, "temp_c": 0,
         "vram_used_mb": 0, "vram_total_mb": 0}
_lock = threading.Lock()

def find_amdgpu_hwmon():
    for d in os.listdir("/sys/class/hwmon/"):
        name_file = f"/sys/class/hwmon/{d}/name"
        try:
            with open(name_file) as f:
                if f.read().strip() == "amdgpu":
                    return f"/sys/class/hwmon/{d}"
        except Exception:
            pass
    return None

def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None

def get_active_freq():
    """Parse pp_dpm_sclk to find the active (*) frequency."""
    raw = read_file(GPU_SCLK)
    if not raw:
        return 0
    for line in raw.split("\n"):
        if "*" in line:
            parts = line.split()
            for p in parts:
                if p.endswith("Mhz"):
                    try:
                        return int(p.replace("Mhz", ""))
                    except ValueError:
                        pass
    return 0

def read_loop():
    global HWMON_DIR, _data
    HWMON_DIR = find_amdgpu_hwmon()
    while True:
        try:
            gpu_pct = 0
            raw = read_file(GPU_BUSY)
            if raw:
                try:
                    gpu_pct = float(raw)
                except ValueError:
                    pass

            freq_mhz = get_active_freq()

            # hwmon: temperature (millidegrees), power (microwatts)
            temp_c = 0
            power_w = 0
            if HWMON_DIR:
                raw_t = read_file(os.path.join(HWMON_DIR, "temp1_input"))
                if raw_t:
                    try:
                        temp_c = round(int(raw_t) / 1000, 1)
                    except ValueError:
                        pass
                raw_p = read_file(os.path.join(HWMON_DIR, "power1_input"))
                if raw_p:
                    try:
                        power_w = round(int(raw_p) / 1000000, 2)
                    except ValueError:
                        pass
                # Also try freq from hwmon
                if freq_mhz == 0:
                    raw_f = read_file(os.path.join(HWMON_DIR, "freq1_input"))
                    if raw_f:
                        try:
                            freq_mhz = round(int(raw_f) / 1000000)
                        except ValueError:
                            pass

            # VRAM
            vram_used = 0
            vram_total = 0
            raw_vu = read_file("/sys/class/drm/card0/device/mem_info_vram_used")
            raw_vt = read_file("/sys/class/drm/card0/device/mem_info_vram_total")
            if raw_vu:
                try:
                    vram_used = int(raw_vu)
                except ValueError:
                    pass
            if raw_vt:
                try:
                    vram_total = int(raw_vt)
                except ValueError:
                    pass

            with _lock:
                _data = {
                    "gpu_pct": round(gpu_pct, 1),
                    "render_pct": round(gpu_pct, 1),  # compat with Intel schema
                    "video_pct": 0,
                    "freq_mhz": freq_mhz,
                    "freq_req": freq_mhz,
                    "rc6_pct": round(100 - gpu_pct, 1),  # idle %
                    "power_gpu": power_w,
                    "power_pkg": power_w,
                    "temp_c": temp_c,
                    "vram_used_mb": round(vram_used / (1024*1024)),
                    "vram_total_mb": round(vram_total / (1024*1024)),
                }
        except Exception:
            pass
        time.sleep(1)

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with _lock:
            body = json.dumps(_data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    t = threading.Thread(target=read_loop, daemon=True)
    t.start()
    time.sleep(0.5)
    srv = http.server.HTTPServer(("0.0.0.0", PORT), H)
    print(f"AMD iGPU monitor on :{PORT}")
    srv.serve_forever()
