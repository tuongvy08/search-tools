#!/usr/bin/env python3
"""systemd worker: python scripts/import_worker.py [--once]. Never logs payloads."""
import argparse
import signal
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from import_jobs import run_once, cleanup
import regulatory_import_jobs


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        try:
            cleanup()
            regulatory_import_jobs.cleanup()
            worked=run_once()
            if not worked:
                worked=regulatory_import_jobs.run_once()
        except Exception:
            print('Import worker: lỗi kết nối/cấu hình; sẽ thử lại.',file=sys.stderr)
            worked=False
        if args.once:
            break
        if not worked:
            stop.wait(5)


if __name__=='__main__':
    main()
