import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root; run from the repo root

import asyncio
import json

from ats.api import build_job_profile, screen_bulk

FOLDERS = [
    r"C:\Users\JAYAN\Downloads\resumes_100_edge_cases",
    r"C:\Users\JAYAN\Downloads\india_typo_resumes",
]

JOB = build_job_profile(
    title="AI Engineer",
    description=(
        "We need engineers with strong Python backend experience, REST API design, "
        "containers, cloud, and production data systems."
    ),
    required_skills=["python", "sql", "docker", "kubernetes", "fastapi"],
    preferred_skills=["redis", "linux", "terraform", "kafka"],
    min_experience_years=0,
    required_education="bachelor",
)


async def main() -> None:
    summaries = []
    for folder in FOLDERS:
        output = await screen_bulk(
            resume_folder=folder,
            job=JOB,
            top_n=10,
            output_json_path=None,
        )
        summaries.append(
            {
                "folder": folder,
                "submitted": output.total_submitted,
                "processed": output.total_processed,
                "filtered": output.total_filtered,
                "failed": output.total_failed,
                "throughput": output.throughput_per_second,
                "avg_score": output.summary_stats.get("avg_score"),
                "most_common_missing_skill": output.summary_stats.get("most_common_missing_skill"),
                "cache_hits": output.summary_stats.get("cache_hits"),
                "cache_hit_rate": output.summary_stats.get("cache_hit_rate"),
                "top1": output.top_candidates[0].name if output.top_candidates else None,
            }
        )
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
