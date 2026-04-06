import asyncio

from ats.api import build_job_profile, screen_bulk, screen_single


async def main() -> None:
    job = build_job_profile(
        title="Senior Backend Engineer",
        description=(
            "We need engineers with strong Python backend experience, REST API design, "
            "containers, cloud, and production data systems."
        ),
        required_skills=["python", "sql", "docker", "kubernetes", "fastapi"],
        preferred_skills=["redis", "linux", "terraform", "kafka"],
        min_experience_years=3.0,
        required_education="bachelor",
    )

    single = await screen_single("sample_resumes/jane_smith.txt", job, debug=True)
    print("\nSINGLE_STATUS", single.status, "SINGLE_SCORE", single.scores.final)

    bulk = await screen_bulk(
        resume_folder="sample_resumes",
        job=job,
        top_n=3,
        output_json_path="ats/output/results_smoke.json",
    )
    print("\nBULK_SUBMITTED", bulk.total_submitted)
    print("BULK_PROCESSED", bulk.total_processed)
    print("BULK_FILTERED", bulk.total_filtered)
    print("BULK_FAILED", bulk.total_failed)
    print("TOP1", bulk.top_candidates[0].name if bulk.top_candidates else "NONE")


if __name__ == "__main__":
    asyncio.run(main())
