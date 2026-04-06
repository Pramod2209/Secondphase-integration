from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional, Tuple

import numpy as np
from rich.console import Console

from . import config
from .embedder import Embedder, encode_batch_worker, encode_single_worker
from .extractor import extract_all, extract_batch, load_taxonomy
from .logger import Timer, get_logger
from .matcher import match
from .parser import parse_docx, parse_file, read_raw_fast
from .prefilter import prefilter
from .resources import detect_and_tune
from .schema import ATSOutput, ExtractionResult, JobProfile, ResumeResult, ScoreBreakdown, SystemResources
from .scorer import compute_scores, generate_explanation

logger = get_logger(__name__)
console = Console()
FEATURE_CACHE_DIR = config.CACHE_DIR / "features"
FEATURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _empty_score() -> ScoreBreakdown:
    return ScoreBreakdown(skills=0.0, experience=0.0, semantic=0.0, education=0.0, final=0.0)


def _candidate_name(file_path: str) -> str:
    return Path(file_path).stem.replace("_", " ").replace("-", " ").title()


def _chunk(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text[:2000].encode("utf-8", errors="ignore")).hexdigest()[:16]


def _feature_cache_path(text: str) -> Path:
    return FEATURE_CACHE_DIR / f"{_text_hash(text)}.json"


def _load_cached_features(text: str) -> Optional[ExtractionResult]:
    cache_path = _feature_cache_path(text)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return ExtractionResult(
            skills=list(payload.get("skills", [])),
            experience_years=float(payload.get("experience_years", 0.0)),
            education_level=str(payload.get("education_level", "none")),
        )
    except Exception:
        return None


def _save_cached_features(text: str, result: ExtractionResult) -> None:
    cache_path = _feature_cache_path(text)
    payload = {
        "skills": result.skills,
        "experience_years": result.experience_years,
        "education_level": result.education_level,
    }
    try:
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _prefilter_text_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".txt":
        return read_raw_fast(path)
    if suffix == ".docx":
        try:
            return parse_docx(path).lower()
        except Exception:
            return ""
    return ""


def _build_failed(path: str, message: str, stage_timings: Optional[Dict[str, float]] = None) -> ResumeResult:
    return ResumeResult(
        candidate_id=str(uuid.uuid4()),
        file_path=path,
        name=_candidate_name(path),
        parsed_text="",
        extracted_skills=[],
        experience_years=0.0,
        education_level="none",
        embedding=None,
        scores=_empty_score(),
        missing_skills=[],
        matched_skills=[],
        explanation="",
        status="failed",
        stage_timings=stage_timings or {},
        error_message=message,
    )


def _build_filtered(path: str) -> ResumeResult:
    return ResumeResult(
        candidate_id=str(uuid.uuid4()),
        file_path=path,
        name=_candidate_name(path),
        parsed_text="",
        extracted_skills=[],
        experience_years=0.0,
        education_level="none",
        embedding=None,
        scores=_empty_score(),
        missing_skills=[],
        matched_skills=[],
        explanation="Filtered out by pre-filter",
        status="filtered",
        stage_timings={},
    )


async def _process_batch(
    batch_paths: List[str],
    job: JobProfile,
    taxonomy: Dict[str, List[str]],
    jd_embedding: np.ndarray,
    resources: SystemResources,
    sem: asyncio.Semaphore,
    thread_pool: ThreadPoolExecutor,
    process_pool: ProcessPoolExecutor,
) -> Tuple[List[ResumeResult], int]:
    async with sem:
        loop = asyncio.get_running_loop()
        batch_start = perf_counter()
        logger.info("Batch start: size=%s", len(batch_paths))

        # A) raw reads for prefilter
        raw_tasks = [loop.run_in_executor(thread_pool, _prefilter_text_for_path, path) for path in batch_paths]
        raw_texts = await asyncio.gather(*raw_tasks, return_exceptions=True)

        passed_paths: List[str] = []
        results: List[ResumeResult] = []
        for path, raw in zip(batch_paths, raw_texts):
            if isinstance(raw, Exception):
                results.append(_build_failed(path, f"raw_read_error: {raw}"))
                continue
            raw_text = str(raw)
            suffix = Path(path).suffix.lower()
            if suffix != ".txt" and len(raw_text.strip()) < 150:
                passed_paths.append(path)
                continue
            if not prefilter(raw_text, job, min_hits=config.PREFILTER_MIN_HITS):
                results.append(_build_filtered(path))
                continue
            passed_paths.append(path)

        # B) parse full text
        parse_tasks = [loop.run_in_executor(thread_pool, parse_file, path) for path in passed_paths]
        parsed = await asyncio.gather(*parse_tasks, return_exceptions=True)

        parsed_paths: List[str] = []
        parsed_texts: List[str] = []
        parse_timings: Dict[str, float] = {}

        for path, item in zip(passed_paths, parsed):
            if isinstance(item, Exception):
                results.append(_build_failed(path, f"parse_error: {item}"))
                continue
            text, parse_time = item
            parse_timings[path] = parse_time
            if not text.strip():
                results.append(_build_failed(path, "empty_parse_output", {"parse": parse_time}))
                continue
            parsed_paths.append(path)
            parsed_texts.append(text)

        if not parsed_texts:
            logger.info("Batch done: parsed=0 filtered_or_failed=%s", len(results))
            return results, 0

        # C) extraction with disk cache, then process pool for misses only
        extracted: List[Optional[ExtractionResult]] = [None] * len(parsed_texts)
        missing_indices: List[int] = []
        missing_texts: List[str] = []
        for idx, text in enumerate(parsed_texts):
            cached = _load_cached_features(text)
            if cached is None:
                missing_indices.append(idx)
                missing_texts.append(text)
            else:
                extracted[idx] = cached

        with Timer() as extract_timer:
            if missing_texts:
                fresh = await loop.run_in_executor(process_pool, extract_batch, missing_texts, taxonomy)
                for idx, item in zip(missing_indices, fresh):
                    extracted[idx] = item
                    _save_cached_features(parsed_texts[idx], item)

        extracted = [item for item in extracted if item is not None]

        # D) embedding in process pool with cache
        with Timer() as embed_timer:
            embeddings, cache_hits = await loop.run_in_executor(
                process_pool,
                encode_batch_worker,
                parsed_texts,
                resources.embedding_batch_size,
            )

        # E) match + score + explanation
        for idx, path in enumerate(parsed_paths):
            data: ExtractionResult = extracted[idx]
            emb = embeddings[idx]
            with Timer() as match_timer:
                cosine, matched, missing, pref_bonus = match(emb, jd_embedding, data.skills, job)
            with Timer() as score_timer:
                score = compute_scores(
                    matched_skills=matched,
                    missing_skills=missing,
                    preferred_bonus=pref_bonus,
                    experience_years=float(data.experience_years),
                    cosine_similarity=cosine,
                    education_level=str(data.education_level),
                    job=job,
                )

            resume = ResumeResult(
                candidate_id=str(uuid.uuid4()),
                file_path=path,
                name=_candidate_name(path),
                parsed_text=parsed_texts[idx],
                extracted_skills=list(data.skills),
                experience_years=float(data.experience_years),
                education_level=str(data.education_level),
                embedding=emb,
                scores=score,
                missing_skills=missing,
                matched_skills=matched,
                explanation="",
                status="processed",
                stage_timings={
                    "parse": round(parse_timings.get(path, 0.0), 4),
                    "extract": round(extract_timer.elapsed / max(1, len(parsed_paths)), 4),
                    "embed": round(embed_timer.elapsed / max(1, len(parsed_paths)), 4),
                    "match": round(match_timer.elapsed, 4),
                    "score": round(score_timer.elapsed, 4),
                },
            )
            resume.explanation = generate_explanation(resume, job)
            results.append(resume)

        elapsed = perf_counter() - batch_start
        filtered_count = sum(1 for r in results if r.status == "filtered")
        failed_count = sum(1 for r in results if r.status == "failed")
        processed_count = sum(1 for r in results if r.status == "processed")
        logger.info(
            "Batch done in %.2fs: processed=%s filtered=%s failed=%s",
            elapsed,
            processed_count,
            filtered_count,
            failed_count,
        )
        return results, cache_hits


def _score_distribution(processed: List[ResumeResult]) -> Dict[str, int]:
    dist = {"80-100": 0, "60-80": 0, "40-60": 0, "0-40": 0}
    for r in processed:
        s = r.scores.final
        if s >= 80:
            dist["80-100"] += 1
        elif s >= 60:
            dist["60-80"] += 1
        elif s >= 40:
            dist["40-60"] += 1
        else:
            dist["0-40"] += 1
    return dist


def _render_bulk_summary(output: ATSOutput) -> None:
    console.print("╔══════════════════════════════════════════════════════╗")
    console.print("║             ATS BULK RUN — COMPLETE                  ║")
    console.print("╚══════════════════════════════════════════════════════╝")
    console.print(f"\n  Total Submitted  : {output.total_submitted}")
    console.print(f"  Processed        : {output.total_processed}")
    console.print(f"  Pre-filtered     : {output.total_filtered}")
    console.print(f"  Failed           : {output.total_failed}")
    console.print(f"  Time Taken       : {output.time_taken_seconds:.1f}s")
    console.print(f"  Throughput       : {output.throughput_per_second:.1f} resumes/sec")

    dist = output.summary_stats.get("score_distribution", {})
    console.print("\n  Score Distribution:")
    for label in ("80-100", "60-80", "40-60", "0-40"):
        count = int(dist.get(label, 0))
        bar = "█" * min(20, max(1, count // 10 if count > 0 else 0))
        console.print(f"    {label:<6} : {bar:<20} {count} candidates")

    m = output.summary_stats.get("most_common_missing_skill", "n/a")
    m_count = output.summary_stats.get("most_common_missing_skill_count", 0)
    cache_hits = int(output.summary_stats.get("cache_hits", 0))
    cache_total = max(1, output.total_processed)
    hit_rate = float(output.summary_stats.get("cache_hit_rate", 0.0)) * 100.0
    console.print(f"\n  Most Common Missing Skill : {m} ({m_count} resumes)")
    console.print(f"  Cache Hits                : {cache_hits} / {output.total_processed} ({hit_rate:.1f}%)")

    console.print("\n  TOP 10 CANDIDATES:")
    console.print("  ──────────────────────────────────────────────────────")
    for idx, item in enumerate(output.top_candidates[:10], start=1):
        skills_preview = ", ".join(item.matched_skills[:3])
        console.print(f"  #{idx:<2}  {item.name:<18} {item.scores.final:>5.1f}%   [{skills_preview}]")


async def run_bulk(
    resume_paths: List[str],
    job: JobProfile,
    top_n: int = 10,
    resources: Optional[SystemResources] = None,
) -> ATSOutput:
    start = perf_counter()
    resources = resources or detect_and_tune()
    taxonomy = load_taxonomy()

    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=resources.process_workers) as process_pool:
        jd_embedding, _ = await loop.run_in_executor(
            process_pool,
            encode_single_worker,
            job.description,
            resources.embedding_batch_size,
        )

        batches = _chunk(resume_paths, resources.batch_size)
        sem = asyncio.Semaphore(resources.max_concurrent_batches)

        all_results: List[ResumeResult] = []
        total_cache_hits = 0

        with ThreadPoolExecutor(max_workers=resources.thread_workers) as thread_pool:
            tasks = [
                _process_batch(
                    batch_paths=batch,
                    job=job,
                    taxonomy=taxonomy,
                    jd_embedding=jd_embedding,
                    resources=resources,
                    sem=sem,
                    thread_pool=thread_pool,
                    process_pool=process_pool,
                )
                for batch in batches
            ]
            batch_outputs = await asyncio.gather(*tasks)
            for batch_results, batch_cache_hits in batch_outputs:
                all_results.extend(batch_results)
                total_cache_hits += batch_cache_hits

    processed = [r for r in all_results if r.status == "processed"]
    filtered = [r for r in all_results if r.status == "filtered"]
    failed = [r for r in all_results if r.status == "failed"]

    processed_sorted = sorted(processed, key=lambda x: x.scores.final, reverse=True)
    top_candidates = processed_sorted[: max(0, min(top_n, len(processed_sorted)))]

    missing_counter = Counter(
        skill for item in processed for skill in item.missing_skills if skill
    )
    common_skill, common_count = (missing_counter.most_common(1)[0] if missing_counter else ("none", 0))

    total_time = perf_counter() - start
    throughput = (len(resume_paths) / total_time) if total_time > 0 else 0.0

    summary_stats = {
        "avg_score": round(
            (sum(r.scores.final for r in processed) / len(processed)) if processed else 0.0,
            2,
        ),
        "score_distribution": _score_distribution(processed),
        "most_common_missing_skill": common_skill,
        "most_common_missing_skill_count": common_count,
        "cache_hits": total_cache_hits,
        "cache_hit_rate": round((total_cache_hits / len(processed)) if processed else 0.0, 3),
    }

    output = ATSOutput(
        job_id=job.job_id,
        total_submitted=len(resume_paths),
        total_processed=len(processed),
        total_filtered=len(filtered),
        total_failed=len(failed),
        time_taken_seconds=round(total_time, 2),
        throughput_per_second=round(throughput, 2),
        top_candidates=top_candidates,
        all_results=all_results,
        summary_stats=summary_stats,
    )

    logger.info(
        "Bulk complete: submitted=%s processed=%s filtered=%s failed=%s time=%.2fs throughput=%.2f/s",
        output.total_submitted,
        output.total_processed,
        output.total_filtered,
        output.total_failed,
        output.time_taken_seconds,
        output.throughput_per_second,
    )
    _render_bulk_summary(output)
    return output


async def run_single(resume_path: str, job: JobProfile, debug: bool = True) -> ResumeResult:
    resources = detect_and_tune()
    taxonomy = load_taxonomy()

    result = _build_failed(resume_path, "unknown")
    total_start = perf_counter()

    if debug:
        console.print("╔══════════════════════════════════════════════════════╗")
        console.print("║           ATS PIPELINE — DEBUG MODE                  ║")
        console.print(f"║  File : {Path(resume_path).name:<44}║")
        role = (job.title or "").strip()[:44]
        console.print(f"║  Role : {role:<44}║")
        console.print("╚══════════════════════════════════════════════════════╝")

    with Timer() as t_parse:
        parsed_text, _ = parse_file(resume_path)
    if not parsed_text:
        result.error_message = "parse_failed"
        result.stage_timings["parse"] = round(t_parse.elapsed, 4)
        return result

    if debug:
        console.print(f"\n[STEP 1 — PARSE]               ✓  {t_parse.elapsed:.2f}s")
        console.print(f"  Preview: \"{parsed_text[:90]}...\"")

    with Timer() as t_extract:
        extracted = extract_all(parsed_text, taxonomy)
    skills = extracted.skills
    if debug:
        console.print(f"\n[STEP 2 — EXTRACT SKILLS]      ✓  {t_extract.elapsed:.2f}s")
        console.print(f"  Found ({len(skills)}): {', '.join(skills[:12])}")

    experience_years = float(extracted.experience_years)
    if debug:
        console.print(f"\n[STEP 3 — EXTRACT EXPERIENCE]  ✓  {t_extract.elapsed:.2f}s")
        console.print(f"  Detected: {experience_years:.1f} years")

    education_level = str(extracted.education_level)
    if debug:
        console.print(f"\n[STEP 4 — EXTRACT EDUCATION]   ✓  {t_extract.elapsed:.2f}s")
        console.print(f"  Level: {education_level}")

    embedder = Embedder(batch_size=resources.embedding_batch_size)
    jd_embedding, _ = embedder.encode_single(job.description)
    with Timer() as t_embed:
        embedding, cache_hit = embedder.encode_single(parsed_text)

    if debug:
        cache_suffix = "[CACHE HIT]" if cache_hit else "[CACHE MISS]"
        console.print(f"\n[STEP 5 — EMBED]               ✓  {t_embed.elapsed:.2f}s  {cache_suffix}")
        console.print(f"  Vector shape : {embedding.shape}  |  Norm: {np.linalg.norm(embedding):.4f}")

    with Timer() as t_match:
        cosine, matched, missing, preferred_bonus = match(embedding, jd_embedding, skills, job)

    if debug:
        console.print(f"\n[STEP 6 — MATCH]               ✓  {t_match.elapsed:.2f}s")
        console.print(f"  Cosine similarity  : {cosine:.4f}")
        console.print(
            f"  Matched skills     : {', '.join(matched) or 'none'}  ({len(matched)}/{len(job.required_skills)} required)"
        )
        console.print(f"  Missing skills     : {', '.join(missing) or 'none'}")
        matched_pref = sorted(set(skills) & set(job.preferred_skills))
        console.print(f"  Preferred bonus    : +{preferred_bonus:.1f} ({', '.join(matched_pref) or 'none'} matched)")

    with Timer() as t_score:
        scores = compute_scores(
            matched_skills=matched,
            missing_skills=missing,
            preferred_bonus=preferred_bonus,
            experience_years=experience_years,
            cosine_similarity=cosine,
            education_level=education_level,
            job=job,
        )

    if debug:
        console.print(f"\n[STEP 7 — SCORE]               ✓  {t_score.elapsed:.2f}s")
        console.print(f"  Skills Score       : {scores.skills:.2f}")
        console.print(f"  Experience Score   : {scores.experience:.2f}")
        console.print(f"  Semantic Score     : {scores.semantic:.2f}")
        console.print(f"  Education Score    : {scores.education:.2f}")
        console.print("  ───────────────────────────")
        console.print(f"  FINAL SCORE        : {scores.final:.2f}")

    result = ResumeResult(
        candidate_id=str(uuid.uuid4()),
        file_path=resume_path,
        name=_candidate_name(resume_path),
        parsed_text=parsed_text,
        extracted_skills=list(skills),
        experience_years=experience_years,
        education_level=education_level,
        embedding=embedding,
        scores=scores,
        missing_skills=missing,
        matched_skills=matched,
        explanation="",
        status="processed",
        stage_timings={
            "parse": round(t_parse.elapsed, 4),
            "extract": round(t_extract.elapsed, 4),
            "embed": round(t_embed.elapsed, 4),
            "match": round(t_match.elapsed, 4),
            "score": round(t_score.elapsed, 4),
        },
    )

    result.explanation = generate_explanation(result, job)
    if debug:
        console.print("\n[STEP 8 — EXPLANATION]")
        console.print(f"  {result.explanation}")
        console.print("\n══════════════════════════════════════════════════════")
        console.print(f"  Total pipeline time: {perf_counter() - total_start:.2f}s")
        console.print("══════════════════════════════════════════════════════")

    return result
