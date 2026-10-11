"""Simple local benchmark to run `screen_bulk` over a folder and report timings.
Usage:
    python tools/benchmark.py <folder_path> [--top-n 10]

The script imports the local package and runs `screen_bulk` directly to avoid HTTP overhead.
"""
import sys
import asyncio
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ats.api import build_job_profile, screen_bulk
from ats.orchestrator import render_bulk_summary

async def run(folder_path: str, top_n: int = 10):
    folder = Path(folder_path)
    if not folder.exists():
        print("Folder not found:", folder)
        return
    job = build_job_profile(
        title="Benchmark Job",
        description="Benchmarking run",
        required_skills=["python"],
        preferred_skills=[],
        min_experience_years=0,
        required_education="any",
    )
    start = datetime.now()
    output = await screen_bulk(str(folder), job, top_n=top_n)
    end = datetime.now()
    dur = (end - start).total_seconds()
    render_bulk_summary(output)
    print(f"Benchmark completed: {dur:.2f}s")
    print(f"Submitted: {output.total_submitted}, Processed: {output.total_processed}, "
          f"Filtered: {output.total_filtered}, Failed: {output.total_failed}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python tools/benchmark.py <folder_path> [--top-n N]")
        sys.exit(1)
    folder = sys.argv[1]
    top_n = 10
    if "--top-n" in sys.argv:
        idx = sys.argv.index("--top-n")
        try:
            top_n = int(sys.argv[idx+1])
        except Exception:
            pass
    asyncio.run(run(folder, top_n=top_n))
