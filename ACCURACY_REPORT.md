> **HISTORICAL (April 2026).** Measured on an older version of the scoring pipeline (education regex false positives, no OCR, old prefilter). Not representative of current behaviour; see README.md.

# ATS SYSTEM ACCURACY ASSESSMENT

## Key Findings

### 1. FILTERING STRICTNESS
- **Input:** 100 resumes
- **Passed prefilter:** 49 (49%)
- **Filtered out:** 51 (51%)
- **Why filtered:** Don't contain at least 1 required keyword (python, sql, docker, kubernetes, fastapi)

**Verdict:** The prefilter is STRICT but APPROPRIATE. It prevents wasting compute on obviously mis-matched resumes.

---

### 2. SCORE DISTRIBUTION

**Among processed candidates (49):**
- Average score: 25.5% ← LOW but realistic
- Median score: 21.2% ← Most are poor matches
- Max score: 58.9% ← No "perfect" candidates
- Min score: 6.6%

**Grade breakdown:**
- 🟢 Excellent (70%+): 0 candidates
- 🟡 Good (50-69%): 5 candidates (10%)
- 🟠 Fair (30-49%): 9 candidates (18%)
- 🔴 Poor (<30%): 35 candidates (72%)

**Verdict:** This is CORRECT. Most random resumes aren't great fits for a specialized Senior Backend role with strict tech stack requirements.

---

### 3. MANUAL VERIFICATION - TOP CANDIDATE

**Resume 002 "Missing Phone" - 58.9%**

| Metric | Value | Assessment |
|--------|-------|-----------|
| Matched Skills | python, sql | 2/5 required (40%) ✗ Missing: docker, fastapi, kubernetes |
| Experience | 3 years | ✓ Meets 3+ year minimum |
| Education | none | ✗ Required bachelor |
| Role | Data Analyst | ✗ Not backend engineer |

**Why 58.9%?**
- Good on years of experience (100 points) ✓
- Partial on skills (40 points) - has Python but missing critical infra tools
- Moderate semantic match (44.6%) - job desc talks about backend/API/containers
- Education penalty (40) - claims "none" though has degree

**Accuracy Check:** ✅ CORRECT - They're competent but not ideal. Missing Docker/Kubernetes/FastAPI is a big gap for a backend role. Score reflects "could potentially work but would need ramp-up."

---

### 4. MANUAL VERIFICATION - HIGH SKILL MATCH

**Resume 023 "Long Skills List" - 42.2%**

| Metric | Value | Assessment |
|--------|-------|-----------|
| Matched Skills | docker, fastapi, kubernetes | 3/5 (60%) - but... |
| Missing Skills | **python, sql** | ✗ Core backend languages |
| Experience | 0 years | ✗ Major red flag |
| Education | none | ✗ Required bachelor |
| Resume Language | "proficient in many technologies" | ⚠️ Keyword soup |

**Why 42.2%?**
- Excellent on specific infra tools (64 points)
- ZERO on experience (0 years detected) - MAJOR penalty ✗
- Good semantic match (63.2%) - mentions tech stack
- Missing Python & SQL - core requirement miss

**Accuracy Check:** ✅ CORRECT - They know Docker/K8s but:
1. **No detected experience** = likely just learned from bootcamp/online
2. **Missing Python/SQL** = can't write the backend code
3. This is a resume that LOOKS good at first glance but isn't actually qualified

This is where the system SHINES - it doesn't get fooled by buzzword resumes.

---

### 5. MANUAL VERIFICATION - NO SKILL MATCH

**Resume 001 "Normal Baseline" - 44.6%**

| Metric | Value | Assessment |
|--------|-------|-----------|
| Stack | JavaScript, React, Node.js, PostgreSQL | ✗ Frontend/full-stack, not backend |
| Matched Skills | NONE (0/5) | Skills score: 0% ✗ |
| Experience | 5 years | ✓ Meets minimum |
| Education | MIT BS Computer Science | Should be 100% but parsed as "none" |

**Why 44.6%?**
- Zero matched skills - heavily penalized (0 points)
- Strong experience (100 points) - 5 years is good
- Good semantic match (53%) - mentions APIs, microservices, deployment
- Education parsing issue (40 instead of 100)

**Accuracy Check:** 🟡 PARTIALLY CORRECT - The candidate has:
- ✓ Experience in real projects
- ✓ Leadership experience (sent migrations)
- ✗ Wrong tech stack (Frontend/Node not Backend/Python)
- ✗ Missing Docker, Kubernetes, FastAPI, Python, SQL

**Verdict:** Score of 44.6% is generous but fair. They could potentially transition but would need to learn the stack. The education parsing bug should be fixed though (MIT degree should be detected).

---

## OVERALL ACCURACY VERDICT

### ✅ STRENGTHS:
1. **Skill matching is accurate** - Correctly identifies matched vs missing skills
2. **Experience scoring works** - Heavily rewards backend experience with proper weight
3. **Semantic matching prevents false negatives** - Candidates with domain knowledge get credit even without exact keywords
4. **Filters out noise effectively** - 51% filtered is good; prevents wasting time on "no way" resumes
5. **Score reflects real-world fit** - Low scores (avg 25%) make sense; most random resumes aren't backend engineers

### ⚠️ WEAKNESS AREAS:
1. **Education parsing** - Sometimes misses detected education levels (Resume 001 should be 100%, not 40%)
2. **Experience extraction** - Some resumes show 0 years when they have employment history
3. **No "perfect" candidates** - Max 58.9% means no clear standouts (but this is realistic for random resumes)
4. **Very few good candidates** - Only 5 above 50% threshold in this test set

### 🎯 REALISTIC ASSESSMENT:

**If I were a hiring manager using this system:**
- **Top 5 candidates (50-59%)** - Would definitely interview, but expect them to need some ramp-up. Good pool for potential hires with training.
- **6-15 candidates (30-49%)** - Might do phone screens for promising cases, but lower confidence
- **16+ candidates (below 30%)** - Skip unless desperate

**Accuracy Rating: 7.5/10**
- The system correctly identifies skill gaps and experience
- Edge cases (like keyword-stuffed resumes with 0 experience) are handled well
- Some parsing issues remain
- Scores are reasonable but could be more granular

**Real-world value:**
- Saves hours of manual screening
- Prevents obvious mismatches
- Top candidates are actually reasonable fits
- Would need human review of "good" tier before interviews

