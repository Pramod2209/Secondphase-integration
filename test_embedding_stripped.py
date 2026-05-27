import requests
from pathlib import Path

# Get first 3 resumes for quick test
folder = Path(r"C:/Users/JAYAN/Downloads/resumes_100_edge_cases/resumes")
files = []
for i, p in enumerate(sorted(folder.glob("*"))):
    if p.suffix.lower() in {".pdf", ".docx", ".txt"}:
        files.append(("resumes", (p.name, open(p, "rb"))))
        if len(files) >= 3:
            break

data = {
    "title": "Senior Backend Engineer",
    "description": "Python experience",
    "required_skills": "python",
    "preferred_skills": "",
    "min_experience_years": "0",
    "required_education": "any",
    "top_n": "5",
}

print("Sending request to /api/bulk...")
resp = requests.post("http://127.0.0.1:8000/api/bulk", data=data, files=files)
obj = resp.json()

print("\n=== CHECKING IF EMBEDDINGS ARE STRIPPED ===")
if obj.get("top_candidates"):
    first_result = obj["top_candidates"][0]
    if "embedding" in first_result:
        print("❌ FAILED: Embedding still present in response!")
        print(f"   Embedding size: {len(first_result['embedding'])} values")
    else:
        print("✓ SUCCESS: No embedding in response!")
    
    print(f"\nFirst candidate keys: {list(first_result.keys())}")
    print(f"Name: {first_result.get('name')}")
    print(f"Scores: {first_result.get('scores')}")
else:
    print("No candidates returned")
