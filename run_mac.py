#!/usr/bin/env python3
"""
Mac launcher: starts the orchestrator (background thread) and the dashboard
web server (foreground process). Used by the launchd agent from setup_mac.sh;
also runnable by hand:  .venv/bin/python run_mac.py
"""

import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)  # everything in the orchestrator resolves relative to this


def start_dashboard():
    """Run dashboard_server.py in the foreground of this process's main thread."""
    import dashboard_server
    import argparse
    ap = argparse.Namespace(port=8787)
    import http.server
    server = http.server.HTTPServer(("127.0.0.1", 8787), dashboard_server.Handler)
    print(f"[run_mac] dashboard on http://127.0.0.1:8787", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# dashboard owns the process; orchestrator runs in a thread
t = threading.Thread(target=lambda: __import__("orchestrator_hermes_main").main(), daemon=True)
t.start()
start_dashboard()