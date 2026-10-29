import argparse
import os
import time
from pathlib import Path

from ..store import Store
from .pipelines import GatewayWorker
from .registry import create_registry


def main():
    parser = argparse.ArgumentParser(description="OntoBridge independent Local Gateway worker")
    parser.add_argument("--once", action="store_true", help="Process one persisted batch and exit")
    args = parser.parse_args()
    data = Path(os.environ.get("ONTOBRIDGE_DATA", "var")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    store = Store(os.environ.get("ONTOBRIDGE_DATABASE_URL", f"sqlite:///{data / 'ontobridge.sqlite'}"))
    worker = GatewayWorker(
        store, Path(os.environ.get("ONTOBRIDGE_SOURCE_ROOT", str(data / "sources"))), create_registry()
    )
    try:
        while True:
            worked = worker.tick()
            if args.once:
                return
            if not worked:
                time.sleep(1)
    except KeyboardInterrupt:
        return
    finally:
        store.engine.dispose()
