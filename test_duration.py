#!/usr/bin/env python3
"""Quick test with just 5 resumes to verify duration is returned."""
import requests
import json
from pathlib import Path

resume_folder = Path(r"C:\Users\JAYAN\Downloads\Part 2\Part 2")
resume_files = sorted(list(resume_folder.glob("*.pdf")) + list(resume_folder.glob("*.docx")) + list(resume_folder.glob("*.txt")))[:5]

print(f"Testing with {len(resume_files)} resume files...")

files = [("resumes", (f.name, open(f, "rb"))) for f in resume_files]

data = {
    "title": "Senior Engineer",
    "description": "Test position",
    "required_skills": "Python",
    "preferred_skills": "FastAPI",
    "min_experience_years": "2",
    "required_education": "bachelor",
    "top_n": "5"
}

try:
    response = requests.post(
        "http://localhost:8000/api/bulk",
        data=data,
        files=files,
        timeout=120
    )
    
    if response.status_code == 200:
        result = response.json()
        print("\n✅ SUCCESS!")
        if "execution_summary" in result:
            summary = result["execution_summary"]
            print(f"Duration: {summary.get('duration_seconds')} seconds ✓")
            print(f"Total Submitted: {summary.get('total_submitted')}")
            print(f"Total Processed: {summary.get('total_processed')}")
            print(f"Throughput: {summary.get('throughput_resumes_per_second')} resumes/sec")
        else:
            print("ERROR: No execution_summary in response")
            print("Response keys:", result.keys())
    else:
        print(f"ERROR: {response.status_code}")
        print(response.text[:500])
except Exception as e:
    print(f"EXCEPTION: {e}")
finally:
    for _, (_, f) in files:
        f.close()
