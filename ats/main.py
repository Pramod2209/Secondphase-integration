from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console

from . import config
from .api import build_job_profile, screen_bulk, screen_single
from .resources import detect_and_tune

console = Console()


def _banner() -> None:
    console.print("╔══════════════════════════════════════╗")
    console.print("║         ATS ENGINE v2.0              ║")
    console.print("║   Fast · Accurate · Explainable      ║")
    console.print("╚══════════════════════════════════════╝")


def _resource_block() -> None:
    resources = detect_and_tune()
    console.print("\n  System Resources Detected:")
    console.print("  ┌─────────────────────────────────┐")
    console.print(f"  │ CPU Cores          :  {resources.cpu_cores:<9}│")
    console.print(f"  │ Available RAM      :  {resources.available_ram_gb:<9.1f}GB│")
    console.print(f"  │ Batch Size         :  {resources.batch_size:<9}│")
    console.print(f"  │ Thread Workers     :  {resources.thread_workers:<9}│")
    console.print(f"  │ Process Workers    :  {resources.process_workers:<9}│")
    console.print(f"  │ Embedding Batch    :  {resources.embedding_batch_size:<9}│")
    console.print(f"  │ Embedding Device   :  {config.EMBEDDING_DEVICE:<9}│")
    console.print("  └─────────────────────────────────┘")


def _prompt_job() -> tuple[str, str, list[str], list[str], float, str]:
    title = input("Role title: ").strip()
    description = input("Job description: ").strip()
    required = [s.strip().lower() for s in input("Required skills (comma-separated): ").split(",") if s.strip()]
    preferred = [s.strip().lower() for s in input("Preferred skills (comma-separated): ").split(",") if s.strip()]
    min_exp = float(input("Minimum experience years: ").strip() or "0")
    edu = (input("Required education [any/bachelor/master/phd]: ").strip() or "any").lower()
    return title, description, required, preferred, min_exp, edu


async def _run() -> None:
    _banner()
    _resource_block()

    while True:
        console.print("\n  Select Mode:")
        console.print("  [1]  Single Resume — Debug Mode")
        console.print("  [2]  Bulk Screening")
        console.print("  [3]  Exit")
        mode = input("> ").strip()

        if mode == "3":
            break

        title, description, required, preferred, min_exp, edu = _prompt_job()
        job = build_job_profile(
            title=title,
            description=description,
            required_skills=required,
            preferred_skills=preferred,
            min_experience_years=min_exp,
            required_education=edu,
        )

        if mode == "1":
            resume_path = input("Resume file path: ").strip()
            await screen_single(resume_path=resume_path, job=job, debug=True)
        elif mode == "2":
            folder = input("Resume folder path: ").strip()
            top_n = int(input("Top N candidates to keep: ").strip() or str(config.DEFAULT_TOP_N))
            out = input("Output JSON path (blank for default): ").strip() or None
            await screen_bulk(resume_folder=folder, job=job, top_n=top_n, output_json_path=out)
        else:
            console.print("Invalid option. Please choose 1, 2, or 3.")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
