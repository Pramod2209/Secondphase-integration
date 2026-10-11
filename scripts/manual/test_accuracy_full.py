import requests
from pathlib import Path
import json
import random

# Load all 100 resumes
folder = Path(r"C:/Users/JAYAN/Downloads/resumes_100_edge_cases/resumes")
files = []
for p in sorted(folder.glob("*")):
    if p.suffix.lower() in {".pdf", ".docx", ".txt"}:
        files.append(("resumes", (p.name, open(p, "rb"))))

print(f"Testing with {len(files)} resumes...\n")

data = {
    "title": "Senior Backend Engineer",
    "description": "We need engineers with strong Python backend experience, REST API design, containers, cloud, and production data systems.",
    "required_skills": "python,sql,docker,kubernetes,fastapi",
    "preferred_skills": "redis,linux,terraform,kafka",
    "min_experience_years": "3",
    "required_education": "bachelor",
    "top_n": "15",
}

resp = requests.post("http://127.0.0.1:8000/api/bulk", headers={"X-ATS-Client": "script"}, data=data, files=files)
obj = resp.json()

print("=" * 80)
print("BULK SCREENING RESULTS")
print("=" * 80)
print(f"Total Submitted: {obj['total_submitted']}")
print(f"Total Processed: {obj['total_processed']}")
print(f"Total Filtered (didn't meet min requirements): {obj['total_filtered']}")
print(f"Total Failed: {obj['total_failed']}")
print(f"Throughput: {obj['throughput_per_second']:.1f} resumes/sec")
print(f"Time taken: {obj['time_taken_seconds']:.2f}s")

print("\n" + "=" * 80)
print("TOP 15 CANDIDATES")
print("=" * 80)

for i, c in enumerate(obj['top_candidates'][:15], 1):
    print(f"\n{i}. {c['name']} - SCORE: {c['scores']['final']:.1f}%")
    print(f"   Skills: {c['scores']['skills']:.1f}% | Experience: {c['scores']['experience']:.1f}% | " +
          f"Semantic: {c['scores']['semantic']:.1f}% | Education: {c['scores']['education']:.1f}%")
    print(f"   Experience: {c['experience_years']} years | Education: {c['education_level']}")
    print(f"   Matched skills: {', '.join(c['matched_skills']) if c['matched_skills'] else 'None'}")
    print(f"   Missing skills: {', '.join(c['missing_skills'][:3]) if c['missing_skills'] else 'None'}")

# Save all results to file for analysis
all_results = obj['all_results']
print(f"\n\n" + "=" * 80)
print(f"SAVING ALL {len(all_results)} RESULTS TO FILE FOR ANALYSIS")
print("=" * 80)

output_file = Path("accuracy_test_all_results.json")
output_file.write_text(json.dumps(obj, indent=2, default=str))
print(f"✓ Saved to {output_file}")

# Print some low-scoring candidates too
print("\n\n" + "=" * 80)
print("BOTTOM 5 CANDIDATES (Lowest scores)")
print("=" * 80)

sorted_results = sorted(all_results, key=lambda x: x['scores']['final'], reverse=False)
for i, c in enumerate(sorted_results[:5], 1):
    print(f"\n{i}. {c['name']} - SCORE: {c['scores']['final']:.1f}%")
    print(f"   Experience: {c['experience_years']} years | Education: {c['education_level']}")
    if c['status'] == 'processed':
        print(f"   Matched skills: {', '.join(c['matched_skills']) if c['matched_skills'] else 'None'}")
        print(f"   Reason: {c['explanation'].split('Gaps:')[1][:200] if 'Gaps:' in c['explanation'] else 'See explanation'}")
    else:
        print(f"   Status: {c['status']}")
