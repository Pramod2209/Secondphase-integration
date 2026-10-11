#!/usr/bin/env python3
"""Test bulk upload with varying file counts."""
import requests
import json
from pathlib import Path

resume_folder = Path(r"C:\Users\JAYAN\Downloads\Part 2\Part 2")
all_files = sorted(list(resume_folder.glob("*.pdf")) + list(resume_folder.glob("*.docx")) + list(resume_folder.glob("*.txt")))

print(f"Total available files: {len(all_files)}")

# Test with different counts
for count in [10, 50, 100, 500, 1000, 1500, 1980]:
    if count > len(all_files):
        print(f"\nSkipping {count} (only {len(all_files)} files available)")
        continue
    
    resume_files = all_files[:count]
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
        print(f"\nTesting with {count} files...", end=" ", flush=True)
        response = requests.post(headers={"X-ATS-Client": "script"}, url=
            "http://localhost:8000/api/bulk",
            data=data,
            files=files,
            timeout=300
        )
        
        if response.status_code == 200:
            result = response.json()
            summary = result.get("execution_summary", {})
            print(f"✅ SUCCESS!")
            print(f"   Duration: {summary.get('duration_seconds')}s | Processed: {summary.get('total_processed')}")
        else:
            print(f"❌ Error {response.status_code}: {response.text[:100]}")
            break
    except Exception as e:
        print(f"❌ Exception: {str(e)[:100]}")
        break
    finally:
        for _, (_, f) in files:
            f.close()
