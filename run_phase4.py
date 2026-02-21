"""Phase 4 server launcher — must be run from toyproject root.
Sets WindowsSelectorEventLoopPolicy BEFORE uvicorn creates the event loop,
which is required for asyncpg SSL on Python 3.8 / Windows.

Usage:
    py -3 run_phase4.py
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "phase4_api.main:app",
        host="0.0.0.0",
        port=8001,
        reload=False,   # reload=True spawns subprocess which loses the event loop policy
    )
