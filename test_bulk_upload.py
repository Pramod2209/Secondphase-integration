#!/usr/bin/env python3
"""Test bulk upload API with 2000 resumes from Part 2 folder."""
import requests
import json
from pathlib import Path

# Get resume files from Part 2 folder
resume_folder = Path(r"C:\Users\JAYAN\Downloads\Part 2\Part 2")
resume_files = sorted(list(resume_folder.glob("*.pdf")) + list(resume_folder.glob("*.docx")) + list(resume_folder.glob("*.txt")))

print(f"Found {len(resume_files)} resume files in {resume_folder}")

# Prepare multipart form data
files = [("resumes", (f.name, open(f, "rb"))) for f in resume_files]

data = {
    "title": "Test Position",
    "description": "Senior Software Engineer",
    "required_skills": "Python,FastAPI",
    "preferred_skills": "React,TypeScript",
    "min_experience_years": "2",
    "required_education": "bachelor",
    "top_n": "10"
}

print(f"\nUploading {len(files)} resumes to API...")

try:
    response = requests.post(
        "http://localhost:8000/api/bulk",
        data=data,
        files=files,
        timeout=300
    )
    
    if response.status_code == 200:
        result = response.json()
        summary = result.get("execution_summary", {})
        print(f"\n✅ Upload Successful!")
        print(f"Duration: {summary.get('duration_seconds', 'N/A')} seconds")
        print(f"Total Submitted: {summary.get('total_submitted', 'N/A')}")
        print(f"Total Processed: {summary.get('total_processed', 'N/A')}")
        print(f"Total Filtered: {summary.get('total_filtered', 'N/A')}")
        print(f"Throughput: {summary.get('throughput_resumes_per_second', 'N/A')} resumes/sec")
        print(f"\nTop candidates:\n{json.dumps(result.get('top_candidates', [])[:3], indent=2)}")
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"❌ Exception: {e}")
finally:
    # Close all files
    for _, (_, f) in files:
        f.close()
