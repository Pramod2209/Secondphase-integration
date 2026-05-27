#!/usr/bin/env python3
"""Test parallel batch processing with all 1980 resume files."""
import requests
import json
from pathlib import Path
import time

resume_folder = Path(r"C:\Users\JAYAN\Downloads\Part 2\Part 2")
resume_files = sorted(list(resume_folder.glob("*.pdf")) + list(resume_folder.glob("*.docx")) + list(resume_folder.glob("*.txt")))

print(f"Testing parallel batch processing with {len(resume_files)} resume files...")
print(f"(Will be split into {(len(resume_files) + 499) // 500} batches of 500 files each)\n")

files = [("resumes", (f.name, open(f, "rb"))) for f in resume_files]

data = {
    "title": "Senior Software Engineer",
    "description": "Looking for senior engineers with Python experience",
    "required_skills": "Python,FastAPI,System Design",
    "preferred_skills": "React,TypeScript,AWS",
    "min_experience_years": "3",
    "required_education": "bachelor",
    "top_n": "20"
}

try:
    print("Uploading all files to /api/bulk-parallel...")
    start_time = time.time()
    
    response = requests.post(
        "http://localhost:8000/api/bulk-parallel",
        data=data,
        files=files,
        timeout=600  # 10 minute timeout for large batch
    )
    
    elapsed = time.time() - start_time
    
    if response.status_code == 200:
        result = response.json()
        summary = result.get("execution_summary", {})
        
        print(f"\n{'='*60}")
        print(f"✅ PARALLEL BATCH PROCESSING SUCCESSFUL!")
        print(f"{'='*60}")
        print(f"\nOverall Results:")
        print(f"  Duration: {summary.get('duration_seconds')}s")
        print(f"  Total Submitted: {summary.get('total_submitted')}")
        print(f"  Total Processed: {summary.get('total_processed')}")
        print(f"  Total Filtered: {summary.get('total_filtered')}")
        print(f"  Throughput: {summary.get('throughput_resumes_per_second')} resumes/sec")
        
        batch_info = summary.get('batch_processing', {})
        if batch_info:
            print(f"\nBatch Processing Details:")
            print(f"  Number of Batches: {batch_info.get('num_batches')}")
            print(f"  Files per Batch: {batch_info.get('batch_size')}")
            print(f"  Parallelization: {batch_info.get('parallelization')}")
            
            print(f"\nPer-Batch Timing:")
            for batch in batch_info.get('batch_details', []):
                print(f"  Batch {batch['batch_id']}: {batch['files_in_batch']} files in {batch['duration_seconds']}s ({batch['throughput']} resumes/sec)")
        
        print(f"\nTop 5 Candidates:")
        for idx, cand in enumerate(result.get('top_candidates', [])[:5], 1):
            score = cand.get('score', 0)
            name = cand.get('name', 'Unknown')[:40]
            print(f"  #{idx}: {name:<40} - {score:.1f}%")
        
        print(f"\n{'='*60}")
        
    else:
        print(f"❌ Error {response.status_code}")
        print(response.text[:500])
        
except Exception as e:
    print(f"❌ Exception: {e}")
finally:
    for _, (_, f) in files:
        f.close()
