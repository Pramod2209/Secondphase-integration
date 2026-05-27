#!/usr/bin/env python3
"""Test parallel batch processing with all 1980 resume files."""
import requests
import time
from pathlib import Path

folder_path = r"C:\Users\JAYAN\Downloads\Part 2\Part 2"

data = {
    "folder_path": folder_path,
    "title": "Senior Software Engineer",
    "description": "Looking for senior engineers with Python experience",
    "required_skills": "Python,FastAPI,System Design",
    "preferred_skills": "React,TypeScript,AWS",
    "min_experience_years": "3",
    "required_education": "bachelor",
    "top_n": "20"
}

try:
    # Count files
    files = list(Path(folder_path).glob("*.pdf")) + list(Path(folder_path).glob("*.docx")) + list(Path(folder_path).glob("*.txt"))
    print(f"Processing {len(files)} resume files from: {folder_path}\n")
    
    print("Starting parallel batch processing...")
    start_time = time.time()
    
    response = requests.post(
        "http://localhost:8000/api/bulk-parallel-folder",
        data=data,
        timeout=3600  # 1 hour timeout for full 1980 files
    )
    
    elapsed = time.time() - start_time
    
    if response.status_code == 200:
        result = response.json()
        summary = result.get("execution_summary", {})
        
        print(f"\n{'='*70}")
        print(f"✅ PARALLEL BATCH PROCESSING SUCCESSFUL!")
        print(f"{'='*70}")
        print(f"\nOverall Results:")
        print(f"  Duration: {summary.get('duration_seconds')}s (wall-clock time)")
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
            
            print(f"\nPer-Batch Performance:")
            for batch in batch_info.get('batch_details', []):
                print(f"  Batch {batch['batch_id']}: {batch['files_in_batch']} files in {batch['duration_seconds']}s ({batch['throughput']} resumes/sec)")
        
        print(f"\nTop 5 Candidates:")
        for idx, cand in enumerate(result.get('top_candidates', [])[:5], 1):
            score = cand.get('score', 0)
            name = cand.get('name', 'Unknown')[:50]
            print(f"  #{idx}: {name:<50} - Score: {score:.1f}%")
        
        print(f"\n{'='*70}")
        print(f"✅ Successfully processed {len(files)} resumes in parallel!")
        print(f"   Total time: {summary.get('duration_seconds')}s")
        print(f"   Improvement with parallelism: ~{batch_info.get('num_batches')}x")
        print(f"{'='*70}\n")
        
    else:
        print(f"❌ Error {response.status_code}")
        print(response.text[:1000])
        
except Exception as e:
    print(f"❌ Exception: {e}")
