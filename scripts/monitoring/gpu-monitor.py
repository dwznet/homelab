#!/usr/bin/env python3
"""Tiny HTTP server that serves nvidia-smi data as JSON on port 9500."""
import subprocess, json
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            out = subprocess.check_output([
                'nvidia-smi',
                '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed',
                '--format=csv,noheader,nounits'
            ], text=True, timeout=5).strip()
            parts = [x.strip() for x in out.split(',')]
            vram_used = float(parts[2])
            vram_total = float(parts[3])
            fan_raw = parts[7] if len(parts) > 7 else '0'
            try:
                fan = float(fan_raw)
            except:
                fan = 0
            data = {
                'name': parts[0],
                'gpu_usage': float(parts[1]),
                'vram_used_mb': vram_used,
                'vram_total_mb': vram_total,
                'vram_pct': round(vram_used / vram_total * 100, 1) if vram_total > 0 else 0,
                'temp': float(parts[4]),
                'power_draw': float(parts[5]),
                'power_limit': float(parts[6]),
                'fan_speed': fan,
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    print("GPU Monitor starting on port 9500...")
    HTTPServer(('0.0.0.0', 9500), Handler).serve_forever()
