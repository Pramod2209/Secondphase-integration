"""Interactive command-line front end: python -m ats.main"""
from __future__ import annotations

import asyncio

from rich.console import Console

from . import config
from .api import JobValidationError, build_job_profile, screen_bulk, screen_single
from .orchestrator import render_bulk_summary
from .parser import _antiword_path, ocr_available
from .resources import describe, detect_and_tune

console = Console(highlight=False)


def _banner() -> None:
    console.print("ATS ENGINE - bulk resume scoring", markup=False)
    console.print(f"  {describe(detect_and_tune())}", markup=False)
    console.print(f"  OCR for scanned PDFs: {'yes' if ocr_available() else 'no (install Tesseract)'} | "
                  f".doc support: {'yes' if _antiword_path() else 'no (install antiword)'} | "
                  f"semantic bulk: {'on' if config.USE_SEMANTIC_EMBEDDING_BULK else 'off'}", markup=False)


def _prompt_job():
    title = input("Role title: ").strip()
    description = input("Job description: ").strip()
    required = input("Required skills (comma-separated): ")
    preferred = input("Preferred skills (comma-separated): ")
    min_exp = input("Minimum experience years [0]: ").strip() or "0"
    edu = input("Required education [any/diploma/bachelor/master/phd]: ").strip() or "any"
    return build_job_profile(title=title, description=description, required_skills=required,
                             preferred_skills=preferred, min_experience_years=min_exp, required_education=edu)


async def _run() -> None:
    _banner()
    while True:
        console.print("\n  [1] Single resume (debug)   [2] Bulk screening   [3] Exit", markup=False)
        mode = input("> ").strip()
        if mode == "3":
            break
        if mode not in ("1", "2"):
            console.print("Invalid option. Please choose 1, 2, or 3.", markup=False)
            continue
        try:
            job = _prompt_job()
        except JobValidationError as exc:
            console.print(f"Invalid job: {exc}", markup=False)
            continue
        try:
            if mode == "1":
                await screen_single(resume_path=input("Resume file path: ").strip().strip('"'), job=job, debug=True)
            else:
                folder = input("Resume folder path: ").strip().strip('"')
                top_n = int(input(f"Top N candidates to keep [{config.DEFAULT_TOP_N}]: ").strip() or config.DEFAULT_TOP_N)
                out = input("Output JSON path (blank for default): ").strip() or None
                output = await screen_bulk(resume_folder=folder, job=job, top_n=top_n, output_json_path=out)
                render_bulk_summary(output)
                console.print(f"\n  Results saved to: {output.summary_stats.get('results_path')}", markup=False)
        except (ValueError, OSError) as exc:
            console.print(f"Error: {exc}", markup=False)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
