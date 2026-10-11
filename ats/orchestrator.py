"""Screening pipeline.

Bulk flow (run_bulk):
    files --> [process pool] parse + extract + job-skill match + chunking   (worker.analyze_file)
          --> prefilter (no job skill found at all -> "filtered")
          --> [1 thread, main process] MiniLM embeddings in batches, overlapped with parsing
          --> score + explanation --> deterministic ranking
A failure in any single file is recorded on that file and never aborts the run.
"""
from __future__ import annotations

import asyncio
import os
import atexit
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from rich.console import Console
from rich.markup import escape

from . import config
from .embedder import Embedder, EmbeddingUnavailable, job_text
from .logger import Timer, get_logger
from .matcher import cosine
from .prefilter import FILTER_REASON, passes_prefilter
from .resources import describe, detect_and_tune
from .schema import ATSOutput, JobProfile, ResumeResult, ScoreBreakdown, SystemResources
from .scorer import compute_scores, generate_explanation, related_line
from .worker import FileAnalysis, analyze_file

logger = get_logger(__name__)
console = Console()

PathItem = Union[str, Tuple[str, str]]  # path, or (path_on_disk, display_path)

# ----------------------------------------------------------------------------- shared executors

_POOL: Optional[ProcessPoolExecutor] = None
_POOL_SIZE = 0
_POOL_LOCK = threading.Lock()
_EMBED_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ats-embed")


def _get_pool(size: int) -> ProcessPoolExecutor:
    """Parser process pool, reused across runs (process start-up is expensive on Windows)."""
    global _POOL, _POOL_SIZE
    with _POOL_LOCK:
        broken = _POOL is not None and getattr(_POOL, "_broken", False)
        if _POOL is None or broken or _POOL_SIZE < size:
            if _POOL is not None:
                _POOL.shutdown(wait=False, cancel_futures=True)
            from .worker import watch_parent

            _POOL = ProcessPoolExecutor(max_workers=size, initializer=watch_parent, initargs=(os.getpid(),))
            _POOL_SIZE = size
        return _POOL


def warm_pool(size: int) -> int:
    """Start the parser processes now (Windows process start-up + imports take several seconds)."""
    from .worker import ping

    pool = _get_pool(size)
    return len({f.result() for f in [pool.submit(ping) for _ in range(size * 2)]})


def _reset_pool() -> None:
    global _POOL, _POOL_SIZE
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL, _POOL_SIZE = None, 0


@atexit.register
def _shutdown_executors() -> None:
    _reset_pool()
    _EMBED_EXECUTOR.shutdown(wait=False, cancel_futures=True)


# ----------------------------------------------------------------------------- progress tracking

PROGRESS: Dict[str, Dict[str, object]] = {}
_PROGRESS_LOCK = threading.Lock()


def _progress(key: Optional[str], **values: object) -> None:
    if not key:
        return
    with _PROGRESS_LOCK:
        entry = PROGRESS.setdefault(key, {"total": 0, "analyzed": 0, "embedded": 0, "stage": "queued",
                                          "started_at": time.time()})
        entry.update(values)
        entry["updated_at"] = time.time()
        if len(PROGRESS) > 200:  # keep memory bounded
            for old in sorted(PROGRESS, key=lambda k: PROGRESS[k].get("updated_at", 0))[:50]:
                PROGRESS.pop(old, None)


def get_progress(key: str) -> Optional[Dict[str, object]]:
    with _PROGRESS_LOCK:
        entry = PROGRESS.get(key)
        return dict(entry) if entry else None


# ----------------------------------------------------------------------------- helpers

def _empty_score() -> ScoreBreakdown:
    return ScoreBreakdown(skills=0.0, experience=0.0, semantic=0.0, education=0.0, final=0.0)


def _normalize_items(items: Sequence[PathItem]) -> List[Tuple[str, str]]:
    out = []
    for item in items:
        if isinstance(item, tuple):
            out.append((str(item[0]), str(item[1])))
        else:
            out.append((str(item), Path(str(item)).name))
    return out


def _jd_vector(job: JobProfile, embedder: Embedder) -> Optional[np.ndarray]:
    text = job_text(job.title, job.description, job.required_skills, job.preferred_skills)
    return embedder.embed_text(text) if text.strip() else None


def _build_result(analysis: FileAnalysis, status: str, job: JobProfile, embedding: Optional[np.ndarray],
                  jd_vec: Optional[np.ndarray], semantic_enabled: bool, embed_time: float) -> ResumeResult:
    stage = {"parse": round(analysis.parse_time, 4), "extract": round(analysis.extract_time, 4),
             "embed": round(embed_time, 4)}
    base = dict(
        candidate_id=str(uuid.uuid4()),
        file_path=analysis.display_path,
        file_name=analysis.file_name,
        name=analysis.name or analysis.file_name,
        name_source=analysis.name_source,
        parsed_text=analysis.text_preview,
        extracted_skills=list(analysis.skills),
        experience_years=float(analysis.experience_years),
        education_level=analysis.education_level,
        embedding=None,
        missing_skills=list(analysis.missing_required),
        matched_skills=list(analysis.matched_required),
        matched_preferred_skills=list(analysis.matched_preferred),
        stage_timings=stage,
        ocr_used=analysis.ocr_used,
        content_hash=analysis.content_hash,
        skill_evidence=list(analysis.skill_evidence),
    )
    if status == "failed":
        from .extractor import name_from_filename

        base.update(name=name_from_filename(analysis.file_name), name_source="filename", missing_skills=[],
                    matched_skills=[], matched_preferred_skills=[], extracted_skills=[], skill_evidence=[])
        return ResumeResult(**base, scores=_empty_score(), explanation="", status="failed",
                            error_message=analysis.error or "unknown_error")
    if status == "filtered":
        related = related_line(analysis.skill_evidence)
        return ResumeResult(**base, scores=_empty_score(), explanation=FILTER_REASON + (f". {related}" if related else ""),
                            status="filtered")

    with Timer() as score_timer:
        sim = cosine(embedding, jd_vec) if semantic_enabled else None
        scores = compute_scores(
            matched_skills=analysis.matched_required,
            missing_skills=analysis.missing_required,
            preferred_bonus=analysis.preferred_bonus,
            experience_years=analysis.experience_years,
            cosine_similarity=sim,
            education_level=analysis.education_level,
            job=job,
            semantic_enabled=semantic_enabled,
            required_credit=analysis.required_credit,
        )
    base["stage_timings"]["score"] = round(score_timer.elapsed, 4)
    result = ResumeResult(**base, scores=scores, explanation="", status="processed")
    result.embedding = embedding
    result.explanation = generate_explanation(result, job, semantic_enabled=semantic_enabled)
    return result


def _rank_key(r: ResumeResult):
    return (-r.scores.final, -r.scores.skills, -r.scores.experience, r.file_path.lower())


def _score_distribution(processed: List[ResumeResult]) -> Dict[str, int]:
    dist = {"80-100": 0, "60-80": 0, "40-60": 0, "0-40": 0}
    for r in processed:
        s = r.scores.final
        key = "80-100" if s >= 80 else "60-80" if s >= 60 else "40-60" if s >= 40 else "0-40"
        dist[key] += 1
    return dist


def render_bulk_summary(output: ATSOutput) -> None:
    """Console summary for CLI use (server runs only log)."""
    print_ = lambda s="": console.print(escape(s), highlight=False)  # noqa: E731
    print_("=" * 56)
    print_("  ATS BULK RUN - COMPLETE")
    print_("=" * 56)
    print_(f"  Total Submitted  : {output.total_submitted}")
    print_(f"  Processed        : {output.total_processed}")
    print_(f"  Pre-filtered     : {output.total_filtered}")
    print_(f"  Failed           : {output.total_failed}")
    print_(f"  Time Taken       : {output.time_taken_seconds:.1f}s")
    print_(f"  Throughput       : {output.throughput_per_second:.1f} resumes/sec")
    dist = output.summary_stats.get("score_distribution", {})
    print_("\n  Score Distribution:")
    for label in ("80-100", "60-80", "40-60", "0-40"):
        count = int(dist.get(label, 0))
        print_(f"    {label:<6} : {'#' * min(40, (count + 9) // 10):<40} {count}")
    reasons = output.summary_stats.get("failure_reasons", {})
    if reasons:
        print_("\n  Failures by reason: " + ", ".join(f"{k}={v}" for k, v in reasons.items()))
    print_(f"\n  Most Common Missing Skill : {output.summary_stats.get('most_common_missing_skill', 'n/a')} "
           f"({output.summary_stats.get('most_common_missing_skill_count', 0)} resumes)")
    print_("\n  TOP CANDIDATES:")
    for item in output.top_candidates[:10]:
        skills_preview = ", ".join(item.matched_skills[:3])
        print_(f"  #{item.rank:<3} {item.name[:24]:<24} {item.scores.final:>6.2f}   [{skills_preview}]")


# ----------------------------------------------------------------------------- bulk

async def run_bulk(
    resume_paths: Sequence[PathItem],
    job: JobProfile,
    top_n: int = config.DEFAULT_TOP_N,
    resources: Optional[SystemResources] = None,
    progress_key: Optional[str] = None,
    semantic: Optional[bool] = None,
    extra_failures: Optional[List[ResumeResult]] = None,
) -> ATSOutput:
    start = perf_counter()
    items = _normalize_items(resume_paths)
    resources = resources or detect_and_tune()
    semantic_enabled = config.USE_SEMANTIC_EMBEDDING_BULK if semantic is None else semantic
    warnings: List[str] = []
    loop = asyncio.get_running_loop()
    _progress(progress_key, total=len(items), stage="analyzing")
    logger.info("Bulk start: files=%s semantic=%s %s", len(items), semantic_enabled, describe(resources))

    embedder = Embedder(batch_size=resources.embedding_batch_size)
    jd_vec: Optional[np.ndarray] = None
    if semantic_enabled and items:
        try:
            Embedder.set_threads(resources.embedding_threads)
            jd_vec = await loop.run_in_executor(_EMBED_EXECUTOR, _jd_vector, job, embedder)
            if jd_vec is None:
                semantic_enabled = False
                warnings.append("Semantic scoring skipped: the job has no title, description or skills to compare.")
        except EmbeddingUnavailable as exc:
            semantic_enabled = False
            warnings.append(f"Semantic scoring unavailable ({exc}); scores use skills/experience/education only.")
            logger.warning("Semantic disabled: %s", exc)

    analyses: Dict[int, FileAnalysis] = {}
    embeddings: Dict[int, Optional[np.ndarray]] = {}
    embed_times: Dict[int, float] = {}
    cache_hits = 0
    embed_failed: List[str] = []
    embed_queue: List[int] = []
    embed_tasks: List[asyncio.Task] = []
    batch_size = max(8, resources.embedding_batch_size * 2)

    async def embed(indices: List[int]) -> None:
        nonlocal cache_hits
        if embed_failed:
            return
        t0 = perf_counter()
        try:
            vectors, hits = await loop.run_in_executor(
                _EMBED_EXECUTOR, embedder.embed_documents, [analyses[i].chunks for i in indices])
        except Exception as exc:  # model failure mid-run: fall back to non-semantic scoring for everyone
            embed_failed.append(f"{type(exc).__name__}: {exc}")
            return
        cache_hits += hits
        per_doc = (perf_counter() - t0) / max(1, len(indices))
        for i, vec in zip(indices, vectors):
            embeddings[i] = vec
            embed_times[i] = per_doc
        _progress(progress_key, embedded=len(embeddings))

    pool_size = max(1, min(resources.parse_workers, len(items)))
    pending = {}
    if items:
        pool = _get_pool(pool_size)
        for idx, (path, display) in enumerate(items):
            fut = loop.run_in_executor(pool, analyze_file, idx, path, display, job, semantic_enabled)
            pending[fut] = idx

    done_count = 0
    for fut in asyncio.as_completed(list(pending)):
        try:
            analysis = await fut
        except (BrokenProcessPool, Exception) as exc:  # worker process died (e.g. out of memory)
            logger.warning("Parser worker failed: %s: %s", type(exc).__name__, exc)
            continue  # the file is retried in-process below
        analyses[analysis.index] = analysis
        done_count += 1
        _progress(progress_key, analyzed=done_count)
        if (analysis.ok and semantic_enabled
                and passes_prefilter(analysis.matched_required, analysis.matched_preferred, job)):
            embed_queue.append(analysis.index)
            if len(embed_queue) >= batch_size:
                embed_tasks.append(asyncio.create_task(embed(embed_queue)))
                embed_queue = []

    # Files whose worker crashed: retry once in-process so nothing is silently lost.
    missing_idx = [i for i in range(len(items)) if i not in analyses]
    if missing_idx:
        _reset_pool()
        logger.warning("Retrying %s file(s) after worker failure", len(missing_idx))
        for i in missing_idx:
            path, display = items[i]
            analyses[i] = await loop.run_in_executor(None, analyze_file, i, path, display, job, semantic_enabled)
            a = analyses[i]
            if a.ok and semantic_enabled and passes_prefilter(a.matched_required, a.matched_preferred, job):
                embed_queue.append(i)

    if embed_queue:
        embed_tasks.append(asyncio.create_task(embed(embed_queue)))
    _progress(progress_key, stage="embedding")
    if embed_tasks:
        await asyncio.gather(*embed_tasks)
    if embed_failed:
        semantic_enabled = False
        warnings.append(f"Embedding failed ({embed_failed[0]}); ranking uses skills/experience/education only.")

    _progress(progress_key, stage="scoring")
    results: List[ResumeResult] = []
    first_by_hash: Dict[str, str] = {}
    for i in range(len(items)):
        a = analyses[i]
        if not a.ok:
            status = "failed"
        elif not passes_prefilter(a.matched_required, a.matched_preferred, job):
            status = "filtered"
        else:
            status = "processed"
        r = _build_result(a, status, job, embeddings.get(i), jd_vec, semantic_enabled, embed_times.get(i, 0.0))
        if a.content_hash:
            if a.content_hash in first_by_hash:
                r.duplicate_of = first_by_hash[a.content_hash]
            else:
                first_by_hash[a.content_hash] = r.file_path
        results.append(r)
    if extra_failures:
        results.extend(extra_failures)

    processed = sorted((r for r in results if r.status == "processed"), key=_rank_key)
    for rank, r in enumerate(processed, start=1):
        r.rank = rank
    top_n = max(0, int(top_n))
    top_candidates = processed[:top_n]
    filtered = [r for r in results if r.status == "filtered"]
    failed = [r for r in results if r.status == "failed"]

    missing_counter = Counter(skill for r in processed for skill in r.missing_skills)
    common_skill, common_count = missing_counter.most_common(1)[0] if missing_counter else ("none", 0)
    total_time = perf_counter() - start
    embedded = sum(1 for v in embeddings.values() if v is not None)
    summary_stats = {
        "avg_score": round(sum(r.scores.final for r in processed) / len(processed), 2) if processed else 0.0,
        "top_score": processed[0].scores.final if processed else 0.0,
        "score_distribution": _score_distribution(processed),
        "most_common_missing_skill": common_skill,
        "most_common_missing_skill_count": common_count,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / embedded, 3) if embedded else 0.0,
        "semantic_enabled": semantic_enabled,
        "prefilter_enabled": config.PREFILTER_ENABLED,
        "failure_reasons": dict(Counter((r.error_message or "unknown").split(":")[0] for r in failed)),
        "ocr_used": sum(1 for r in results if r.ocr_used),
        "duplicate_files": sum(1 for r in results if r.duplicate_of),
        "warnings": warnings,
        "resources": describe(resources),
    }
    output = ATSOutput(
        job_id=job.job_id,
        total_submitted=len(results),
        total_processed=len(processed),
        total_filtered=len(filtered),
        total_failed=len(failed),
        time_taken_seconds=round(total_time, 2),
        throughput_per_second=round(len(results) / total_time, 2) if total_time > 0 else 0.0,
        top_candidates=top_candidates,
        all_results=results,
        summary_stats=summary_stats,
    )
    _progress(progress_key, stage="done", analyzed=len(items))
    logger.info("Bulk complete: submitted=%s processed=%s filtered=%s failed=%s time=%.2fs throughput=%.2f/s",
                output.total_submitted, output.total_processed, output.total_filtered, output.total_failed,
                output.time_taken_seconds, output.throughput_per_second)
    return output


# ----------------------------------------------------------------------------- single

async def run_single(resume_path: str, job: JobProfile, debug: bool = False, display_path: Optional[str] = None,
                     semantic: Optional[bool] = None) -> ResumeResult:
    resources = detect_and_tune()
    semantic_enabled = config.USE_SEMANTIC_EMBEDDING_SINGLE if semantic is None else semantic
    display_path = display_path or Path(resume_path).name
    loop = asyncio.get_running_loop()
    total_start = perf_counter()
    say = (lambda s: console.print(escape(s), highlight=False)) if debug else (lambda s: None)

    analysis = await loop.run_in_executor(None, analyze_file, 0, resume_path, display_path, job, semantic_enabled, 500)
    say(f"[PARSE+EXTRACT] {analysis.parse_time + analysis.extract_time:.2f}s  ok={analysis.ok} "
        f"{analysis.error or ''}")
    if not analysis.ok:
        return _build_result(analysis, "failed", job, None, None, False, 0.0)

    embedding = jd_vec = None
    embed_time = 0.0
    if semantic_enabled:
        embedder = Embedder(batch_size=resources.embedding_batch_size)
        try:
            t0 = perf_counter()
            jd_vec = await loop.run_in_executor(_EMBED_EXECUTOR, _jd_vector, job, embedder)
            vectors, _ = await loop.run_in_executor(_EMBED_EXECUTOR, embedder.embed_documents, [analysis.chunks])
            embedding = vectors[0]
            embed_time = perf_counter() - t0
            if jd_vec is None or embedding is None:
                semantic_enabled = False
        except EmbeddingUnavailable as exc:
            logger.warning("Semantic disabled for single run: %s", exc)
            semantic_enabled = False

    result = _build_result(analysis, "processed", job, embedding, jd_vec, semantic_enabled, embed_time)
    result.rank = 1
    say(f"  Name       : {result.name} (from {result.name_source})")
    say(f"  Skills     : {', '.join(result.extracted_skills[:15])}")
    say(f"  Experience : {result.experience_years:.1f} years | Education: {result.education_level}")
    say(f"  Matched    : {', '.join(result.matched_skills) or 'none'} | Missing: {', '.join(result.missing_skills) or 'none'}")
    say(f"  Scores     : {result.scores}")
    say(result.explanation)
    say(f"  Total pipeline time: {perf_counter() - total_start:.2f}s")
    return result
