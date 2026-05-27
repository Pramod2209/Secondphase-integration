import { useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

const defaultJob = {
  title: "Senior Backend Engineer",
  description:
    "We need engineers with strong Python backend experience, REST API design, containers, cloud, and production data systems.",
  required_skills: "python,sql,docker,kubernetes,fastapi",
  preferred_skills: "redis,linux,terraform,kafka",
  min_experience_years: "3",
  required_education: "bachelor",
};

function buildForm(job, extra = {}) {
  const fd = new FormData();
  Object.entries({ ...job, ...extra }).forEach(([k, v]) => fd.append(k, String(v)));
  return fd;
}

function SectionCard({ title, subtitle, children }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      <p className="subtitle">{subtitle}</p>
      {children}
    </section>
  );
}

function formatSeconds(seconds) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value < 0) return "0.0s";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const remaining = (value - minutes * 60).toFixed(1);
  return `${minutes}m ${remaining}s`;
}

function JobForm({ job, setJob, prefix }) {
  return (
    <div className="grid two">
      <label>
        Role Title
        <input value={job.title} onChange={(e) => setJob({ ...job, title: e.target.value })} />
      </label>
      <label>
        Required Education
        <select
          value={job.required_education}
          onChange={(e) => setJob({ ...job, required_education: e.target.value })}
        >
          <option value="any">any</option>
          <option value="bachelor">bachelor</option>
          <option value="master">master</option>
          <option value="phd">phd</option>
        </select>
      </label>
      <label className="span2">
        Job Description
        <textarea
          rows={4}
          value={job.description}
          onChange={(e) => setJob({ ...job, description: e.target.value })}
        />
      </label>
      <label>
        Required Skills (csv)
        <input
          value={job.required_skills}
          onChange={(e) => setJob({ ...job, required_skills: e.target.value })}
        />
      </label>
      <label>
        Preferred Skills (csv)
        <input
          value={job.preferred_skills}
          onChange={(e) => setJob({ ...job, preferred_skills: e.target.value })}
        />
      </label>
      <label>
        Min Experience (years)
        <input
          type="number"
          step="0.5"
          min="0"
          value={job.min_experience_years}
          onChange={(e) => setJob({ ...job, min_experience_years: e.target.value })}
        />
      </label>
      <div className="badge">{prefix} mode</div>
    </div>
  );
}

export default function App() {
  const [singleJob, setSingleJob] = useState(defaultJob);
  const [bulkJob, setBulkJob] = useState(defaultJob);

  const [singleFile, setSingleFile] = useState(null);
  const [bulkFiles, setBulkFiles] = useState([]);
  const [topN, setTopN] = useState(10);

  const [singleResult, setSingleResult] = useState(null);
  const [bulkResult, setBulkResult] = useState(null);
  const [loadingSingle, setLoadingSingle] = useState(false);
  const [loadingBulk, setLoadingBulk] = useState(false);
  const [error, setError] = useState("");

  const topRows = useMemo(() => bulkResult?.top_candidates || [], [bulkResult]);

  async function runSingle() {
    if (!singleFile) {
      setError("Upload one resume for single mode.");
      return;
    }
    setError("");
    setLoadingSingle(true);
    try {
      const fd = buildForm(singleJob, { debug: true });
      fd.append("resume", singleFile);
      const res = await fetch(`${API_BASE}/api/single`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      setSingleResult(await res.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingSingle(false);
    }
  }

  async function runBulk() {
    if (!bulkFiles.length) {
      setError("Upload one or more resumes for bulk mode.");
      return;
    }
    setError("");
    setLoadingBulk(true);
    try {
      const bulkStart = performance.now();
      const BATCH_SIZE = 900; // Safe limit (Starlette limit is 1000)
      const batches = [];
      for (let i = 0; i < bulkFiles.length; i += BATCH_SIZE) {
        batches.push(bulkFiles.slice(i, i + BATCH_SIZE));
      }

      const batchResults = [];
      for (let bIdx = 0; bIdx < batches.length; bIdx++) {
        const batch = batches[bIdx];
        setError(`Processing batch ${bIdx + 1} of ${batches.length}...`);
        
        const fd = buildForm(bulkJob, { top_n: topN });
        batch.forEach((f) => fd.append("resumes", f));
        
        const res = await fetch(`${API_BASE}/api/bulk`, { method: "POST", body: fd });
        if (!res.ok) {
          const errText = await res.text();
          throw new Error(`Batch ${bIdx + 1} failed: ${errText}`);
        }
        batchResults.push(await res.json());
      }

      // Aggregate results from all batches
      const aggregated = {
        total_submitted: batchResults.reduce((sum, r) => sum + r.total_submitted, 0),
        total_processed: batchResults.reduce((sum, r) => sum + r.total_processed, 0),
        total_filtered: batchResults.reduce((sum, r) => sum + r.total_filtered, 0),
        total_failed: batchResults.reduce((sum, r) => sum + r.total_failed, 0),
        total_time_taken_seconds: (performance.now() - bulkStart) / 1000,
        top_candidates: batchResults
          .flatMap((r) => r.top_candidates || [])
          .sort((a, b) => (b.scores?.final || 0) - (a.scores?.final || 0))
          .slice(0, topN),
        execution_summary: batchResults[batchResults.length - 1]?.execution_summary,
      };

      setBulkResult(aggregated);
      setError("");
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingBulk(false);
    }
  }

  return (
    <main className="page">
      <header className="hero">
        <h1>ATS Playground</h1>
        <p>Quick React UI for single-debug and bulk resume screening.</p>
      </header>

      {error && <div className="error">{error}</div>}

      <div className="layout">
        <SectionCard title="Single Resume" subtitle="Run full debug-style pipeline on one resume.">
          <JobForm job={singleJob} setJob={setSingleJob} prefix="single" />
          <label className="upload">
            Resume (.pdf .docx .txt)
            <input
              type="file"
              accept=".pdf,.docx,.txt"
              onChange={(e) => setSingleFile(e.target.files?.[0] || null)}
            />
          </label>
          <button onClick={runSingle} disabled={loadingSingle}>
            {loadingSingle ? "Running..." : "Run Single"}
          </button>

          {singleResult && (
            <div className="result">
              <h3>Result</h3>
              <p>
                <strong>Status:</strong> {singleResult.status} | <strong>Score:</strong>{" "}
                {singleResult.scores?.final}
              </p>
              <p>
                <strong>Candidate:</strong> {singleResult.name}
              </p>
              <p>
                <strong>Matched:</strong> {(singleResult.matched_skills || []).join(", ") || "none"}
              </p>
              <p>
                <strong>Missing:</strong> {(singleResult.missing_skills || []).join(", ") || "none"}
              </p>
              <details>
                <summary>Raw JSON</summary>
                <pre>{JSON.stringify(singleResult, null, 2)}</pre>
              </details>
            </div>
          )}
        </SectionCard>

        <SectionCard title="Bulk Screening" subtitle="Upload many resumes and view ranked top candidates.">
          <JobForm job={bulkJob} setJob={setBulkJob} prefix="bulk" />
          <label className="upload">
            Resumes (.pdf .docx .txt)
            <input
              type="file"
              accept=".pdf,.docx,.txt"
              multiple
              onChange={(e) => setBulkFiles(Array.from(e.target.files || []))}
            />
          </label>
          <label>
            Top N
            <input type="number" min="1" value={topN} onChange={(e) => setTopN(Number(e.target.value))} />
          </label>
          <button onClick={runBulk} disabled={loadingBulk}>
            {loadingBulk ? "Running..." : "Run Bulk"}
          </button>

          {bulkResult && (
            <div className="result">
              <h3>Summary</h3>
              <p>
                submitted {bulkResult.total_submitted} | processed {bulkResult.total_processed} |
                filtered {bulkResult.total_filtered} | failed {bulkResult.total_failed}
              </p>
              <p>
                <strong>Total time taken:</strong> {formatSeconds(bulkResult.total_time_taken_seconds)}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Score</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {topRows.map((r) => (
                    <tr key={r.candidate_id}>
                      <td>{r.name}</td>
                      <td>{r.scores?.final}</td>
                      <td>{r.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <details>
                <summary>Raw JSON</summary>
                <pre>{JSON.stringify(bulkResult, null, 2)}</pre>
              </details>
            </div>
          )}
        </SectionCard>
      </div>
    </main>
  );
}
