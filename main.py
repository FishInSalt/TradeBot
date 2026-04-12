# main.py

import argparse
import asyncio

from src.cli.app import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeBot")
    parser.add_argument("--model", type=str, default=None, help="Model ID from models.json (skip interactive selection)")
    parser.add_argument("--debug", action="store_true", help="Show all system logs on terminal")
    args = parser.parse_args()
    asyncio.run(run(model_id=args.model, debug=args.debug))
