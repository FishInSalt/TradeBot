# main.py

import argparse
import asyncio

from src.cli.app import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeBot")
    parser.add_argument("--model", type=str, default=None, help="Model ID from models.json (skip interactive selection)")
    args = parser.parse_args()
    asyncio.run(run(model_id=args.model))
