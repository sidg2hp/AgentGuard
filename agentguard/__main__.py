"""Command-line lifecycle and backup tools for a single AgentGuard workspace."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import threading

from agentguard.workspace.config import Settings, init_token
from agentguard.workspace.store import Store


def main():
    parser = argparse.ArgumentParser(prog="agentguard")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize the database and local token file")
    serve = commands.add_parser("serve", help="Serve the workspace console and versioned API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--with-worker", action="store_true", help="Run an embedded worker for local use")
    commands.add_parser("worker", help="Consume jobs from the configured workspace")
    commands.add_parser("status", help="Show queue counts and recent worker heartbeats")
    backup = commands.add_parser("backup", help="Write a consistent SQLite snapshot to a new file")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "init" and not os.getenv("AGENTGUARD_API_TOKEN"):
        print(f"Workspace token file: {init_token()}")
    try:
        settings = Settings.load()
        store = Store(settings.database, capacity=settings.capacity)
        store.initialize()
        if args.command == "init":
            print(f"Database ready: {store.path}")
        elif args.command == "status":
            print(json.dumps(store.stats(), indent=2))
        elif args.command == "backup":
            store.backup(args.destination)
            print(f"Snapshot saved: {args.destination}")
        else:
            from agentguard.workspace.worker import Worker
            stop = threading.Event()
            if args.command == "worker":
                for sig in (signal.SIGINT, signal.SIGTERM):
                    signal.signal(sig, lambda *_: stop.set())
                Worker(store, settings).run(stop)
            else:
                import uvicorn
                from agentguard.workspace.api import create_app
                thread = None
                if args.with_worker:
                    thread = threading.Thread(target=Worker(store, settings).run, args=(stop,), daemon=True)
                    thread.start()
                try:
                    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
                finally:
                    stop.set()
                    if thread:
                        thread.join(timeout=10)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"agentguard: {exc}\n")


if __name__ == "__main__":
    main()
