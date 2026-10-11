import json
from pathlib import Path

# Load the results file
results_file = Path("accuracy_test_all_results.json")
results = json.loads(results_file.read_text())

# Pick 5 random processed candidates to verify manually
processed = [r for r in results['all_results'] if r['status'] == 'processed']
print(f"Total processed: {len(processed)}")

resumes_dir = Path(r"C:/Users/JAYAN/Downloads/resumes_100_edge_cases/resumes")

# Check the TOP candidate
top = results['top_candidates'][0]
print("\n" + "=" * 80)
print(f"VERIFYING: {top['name']}")
print("=" * 80)
print(f"System Score: {top['scores']['final']:.1f}%")
print(f"  - Skills: {top['scores']['skills']:.1f}% ({len(top['matched_skills'])} matched)")
print(f"  - Experience: {top['scores']['experience']:.1f}% ({top['experience_years']} years)")
print(f"  - Semantic: {top['scores']['semantic']:.1f}%")
print(f"  - Education: {top['scores']['education']:.1f}% ({top['education_level']})")
print(f"\nMatched: {', '.join(top['matched_skills']) if top['matched_skills'] else 'None'}")
print(f"Missing: {', '.join(top['missing_skills'])}")

# Show parsed text
print("\n--- PARSED TEXT ---")
print('(resume text is no longer returned by the API)')
print("[... truncated ...]")

# Check another high-confidence candidate
print("\n\n" + "=" * 80)
print("CHECKING: Resume with good skill match")
print("=" * 80)

# Find one with high skill score
high_skill = max([r for r in processed if r['scores']['skills'] > 50], 
                 key=lambda x: x['scores']['skills'])
print(f"Candidate: {high_skill['name']}")
print(f"System Score: {high_skill['scores']['final']:.1f}%")
print(f"  - Skills: {high_skill['scores']['skills']:.1f}% ({len(high_skill['matched_skills'])} matched)")
print(f"  - Experience: {high_skill['scores']['experience']:.1f}% ({high_skill['experience_years']} years)")
print(f"  - Semantic: {high_skill['scores']['semantic']:.1f}%")
print(f"  - Education: {high_skill['scores']['education']:.1f}% ({high_skill['education_level']})")
print(f"\nMatched skills: {', '.join(high_skill['matched_skills'])}")
print(f"Missing skills: {', '.join(high_skill['missing_skills'])}")

print("\n--- PARSED TEXT ---")
print('(resume text is no longer returned by the API)')

# Check resume with no skills matched
print("\n\n" + "=" * 80)
print("CHECKING: Resume with NO skills matched")
print("=" * 80)

no_skills = [r for r in processed if len(r['matched_skills']) == 0][0]
print(f"Candidate: {no_skills['name']}")
print(f"System Score: {no_skills['scores']['final']:.1f}%")
print(f"  - Skills: {no_skills['scores']['skills']:.1f}% (0 matched)")
print(f"  - Experience: {no_skills['scores']['experience']:.1f}% ({no_skills['experience_years']} years)")
print(f"  - Semantic: {no_skills['scores']['semantic']:.1f}%")
print(f"  - Education: {no_skills['scores']['education']:.1f}% ({no_skills['education_level']})")

print("\n--- PARSED TEXT ---")
print('(resume text is no longer returned by the API)')

# Check the explanation
print("\n--- SYSTEM EXPLANATION ---")
print(no_skills['explanation'])

# Summary stats
print("\n\n" + "=" * 80)
print("ACCURACY ANALYSIS")
print("=" * 80)
print(f"\nScore Distribution (processed candidates only):")
scores = [r['scores']['final'] for r in processed]
print(f"  - Average Score: {sum(scores) / len(scores):.1f}%")
print(f"  - Max Score: {max(scores):.1f}%")
print(f"  - Min Score: {min(scores):.1f}%")
print(f"  - Median Score: {sorted(scores)[len(scores)//2]:.1f}%")

excellent = len([s for s in scores if s >= 70])
good = len([s for s in scores if 50 <= s < 70])
fair = len([s for s in scores if 30 <= s < 50])
poor = len([s for s in scores if s < 30])

print(f"\nScore Breakdown:")
print(f"  - Excellent (70%+): {excellent} candidates")
print(f"  - Good (50-69%): {good} candidates")
print(f"  - Fair (30-49%): {fair} candidates")
print(f"  - Poor (<30%): {poor} candidates")

# Check skill matching accuracy
print(f"\nSkill Matching Insight:")
high_skill_match = len([r for r in processed if len(r['matched_skills']) >= 3])
medium_skill_match = len([r for r in processed if len(r['matched_skills']) >= 1 and len(r['matched_skills']) < 3])
zero_skill_match = len([r for r in processed if len(r['matched_skills']) == 0])

print(f"  - 3+ skills matched: {high_skill_match} candidates")
print(f"  - 1-2 skills matched: {medium_skill_match} candidates")
print(f"  - 0 skills matched: {zero_skill_match} candidates (relies on experience & semantic match)")
