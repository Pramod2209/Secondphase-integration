#!/usr/bin/env python3
"""Test parallel batch processing with smaller dataset first."""
import requests
import json
import time
from pathlib import Path
import shutil

# Create a test folder with subset of files
test_folder = Path(r"C:\Users\JAYAN\Downloads\test_subset")
if test_folder.exists():
    shutil.rmtree(test_folder)
test_folder.mkdir()

# Copy first 100 files for quick test
source_folder = Path(r"C:\Users\JAYAN\Downloads\Part 2\Part 2")
source_files = sorted(list(source_folder.glob("*.pdf")) + list(source_folder.glob("*.docx")) + list(source_folder.glob("*.txt")))[:100]

for f in source_files:
    shutil.copy2(f, test_folder / f.name)

print(f"Testing parallel batch processing with {len(source_files)} resume files...")
print(f"Test folder: {test_folder}\n")

data = {
    "folder_path": str(test_folder),
    "title": "Senior Software Engineer",
    "description": "Looking for senior engineers with Python experience",
    "required_skills": "Python,FastAPI,System Design",
    "preferred_skills": "React,TypeScript,AWS",
    "min_experience_years": "3",
    "required_education": "bachelor",
    "top_n": "10"
}

try:
    print("Processing resumes in parallel batches...")
    start_time = time.time()
    
    response = requests.post(headers={"X-ATS-Client": "script"}, url=
        "http://localhost:8000/api/bulk-parallel-folder",
        data=data,
        timeout=120  # 2 minute timeout for 100 files
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
        
        print(f"\nTop 3 Candidates:")
        for idx, cand in enumerate(result.get('top_candidates', [])[:3], 1):
            score = cand.get('score', 0)
            name = cand.get('name', 'Unknown')[:40]
            print(f"  #{idx}: {name:<40} - {score:.1f}%")
        
        print(f"\n{'='*60}")
        print("✅ Parallel batch processing validated!")
        print("Ready for larger datasets.\n")
        
    else:
        print(f"❌ Error {response.status_code}")
        print(response.text[:500])
        
except Exception as e:
    print(f"❌ Exception: {e}")
finally:
    # Cleanup
    shutil.rmtree(test_folder)
    print(f"Cleaned up test folder")
