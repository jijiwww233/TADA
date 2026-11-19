"""Controlled 50-item download benchmark, NOT a live-platform throughput claim.
Uses real local HTTP with 40ms artificial latency, no authentication or cloud API.
"""
import argparse
import concurrent.futures
import io
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import requests
from PIL import Image


def benchmark(count=50):
    buf=io.BytesIO(); Image.new('RGB',(16,16),'beige').save(buf,'PNG'); data=buf.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(.04)
            self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        out={}
        with tempfile.TemporaryDirectory() as folder:
            def download(i):
                response=requests.get(f'http://127.0.0.1:{server.server_port}/{i}',timeout=10); response.raise_for_status()
                assert response.content == data
                (Path(folder)/f'{i}.png').write_bytes(response.content)
                return i
            for platform,after in [('xhs',1),('huaban',1),('pinterest',4),('zcool',4)]:
                record={}
                for phase,workers in [('before',1),('after',after)]:
                    t=time.perf_counter()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                        assert list(pool.map(download,range(count)))==list(range(count))
                    elapsed=time.perf_counter()-t
                    record[phase]={'download_workers':workers,'items':count,'total_seconds':elapsed,'seconds_per_item':elapsed/count}
                out[platform]=record
        return out
    finally: server.shutdown(); server.server_close()

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():ap.error('output exists; choose a new file')
    result=benchmark();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result,indent=2))
