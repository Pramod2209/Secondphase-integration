import { useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
// Sent with every POST; the API rejects requests without it (blocks cross-site form posts).
const CLIENT_HEADERS = { "X-ATS-Client": "webui" };
// Starlette accepts at most 1000 files per multipart request; stay below it.
const BATCH_SIZE = 900;

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

async function readError(res) {
  const text = await res.text();
  try {
    const body = JSON.parse(text);
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ");
  } catch {
    /* not JSON */
  }
  return text || `HTTP ${res.status}`;
}

function newRequestId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function rankCandidates(a, b) {
  const sa = a.scores || {};
  const sb = b.scores || {};
  return (
    (sb.final || 0) - (sa.final || 0) ||
    (sb.skills || 0) - (sa.skills || 0) ||
    (sb.experience || 0) - (sa.experience || 0) ||
    String(a.file_path).localeCompare(String(b.file_path))
  );
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
  return `${minutes}m ${(value - minutes * 60).toFixed(1)}s`;
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
        <select value={job.required_education} onChange={(e) => setJob({ ...job, required_education: e.target.value })}>
          <option value="any">any</option>
          <option value="diploma">diploma</option>
          <option value="bachelor">bachelor</option>
          <option value="master">master</option>
          <option value="phd">phd</option>
        </select>
      </label>
      <label className="span2">
        Job Description
        <textarea rows={4} value={job.description} onChange={(e) => setJob({ ...job, description: e.target.value })} />
      </label>
      <label>
        Required Skills (comma-separated)
        <input value={job.required_skills} onChange={(e) => setJob({ ...job, required_skills: e.target.value })} />
      </label>
      <label>
        Preferred Skills (comma-separated)
        <input value={job.preferred_skills} onChange={(e) => setJob({ ...job, preferred_skills: e.target.value })} />
      </label>
      <label>
        Min Experience (years)
        <input
          type="number"
          step="0.5"
          min="0"
          max="50"
          value={job.min_experience_years}
          onChange={(e) => setJob({ ...job, min_experience_years: e.target.value })}
        />
      </label>
      <div className="badge">{prefix} mode</div>
    </div>
  );
}

function Scores({ s }) {
  if (!s) return null;
  return (
    <span>
      {Number(s.final).toFixed(1)}{" "}
      <small>
        (skills {s.skills} · exp {s.experience} · sem {s.semantic} · edu {s.education})
      </small>
    </span>
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
  const [status, setStatus] = useState("");

  async function runSingle() {
    if (!singleFile) {
      setError("Upload one resume for single mode.");
      return;
    }
    setError("");
    setLoadingSingle(true);
    try {
      const fd = buildForm(singleJob);
      fd.append("resume", singleFile);
      const res = await fetch(`${API_BASE}/api/single`, { method: "POST", body: fd, headers: CLIENT_HEADERS });
      if (!res.ok) throw new Error(await readError(res));
      setSingleResult(await res.json());
    } catch (e) {
      setError(`Single run failed: ${e.message || e}`);
    } finally {
      setLoadingSingle(false);
    }
  }

  async function runBulk() {
    if (!bulkFiles.length) {
      setError("Upload one or more resumes for bulk mode.");
      return;
    }
    const n = Math.max(1, Math.floor(Number(topN) || 10));
    setError("");
    setBulkResult(null);
    setLoadingBulk(true);
    const started = performance.now();
    const batches = [];
    for (let i = 0; i < bulkFiles.length; i += BATCH_SIZE) batches.push(bulkFiles.slice(i, i + BATCH_SIZE));

    const totals = { total_submitted: 0, total_processed: 0, total_filtered: 0, total_failed: 0 };
    const candidates = [];
    const failed = [];
    const warnings = new Set();
    let scoreSum = 0;
    let semanticEnabled = true;
    try {
      for (let b = 0; b < batches.length; b++) {
        const requestId = newRequestId();
        const fd = buildForm(bulkJob, { top_n: n, request_id: requestId });
        batches[b].forEach((f) => fd.append("resumes", f));
        const label = `Batch ${b + 1} of ${batches.length} (${batches[b].length} files)`;
        setStatus(`${label}: uploading...`);
        const poll = setInterval(async () => {
          try {
            const p = await fetch(`${API_BASE}/api/progress/${requestId}`);
            if (p.ok) {
              const info = await p.json();
              setStatus(`${label}: ${info.stage} - analyzed ${info.analyzed}/${info.total}, embedded ${info.embedded}`);
            }
          } catch {
            /* progress is best-effort */
          }
        }, 1000);
        let res;
        try {
          res = await fetch(`${API_BASE}/api/bulk`, { method: "POST", body: fd, headers: CLIENT_HEADERS });
        } finally {
          clearInterval(poll);
        }
        if (!res.ok) throw new Error(`${label} failed: ${await readError(res)}`);
        const r = await res.json();
        totals.total_submitted += r.total_submitted;
        totals.total_processed += r.total_processed;
        totals.total_filtered += r.total_filtered;
        totals.total_failed += r.total_failed;
        scoreSum += (r.summary_stats?.avg_score || 0) * r.total_processed;
        semanticEnabled = semanticEnabled && Boolean(r.summary_stats?.semantic_enabled);
        (r.summary_stats?.warnings || []).forEach((w) => warnings.add(w));
        // Each batch returns its own top N, so the union contains the global top N.
        candidates.push(...(r.top_candidates || []));
        failed.push(...(r.all_results || []).filter((x) => x.status === "failed"));
      }
      const top = candidates.sort(rankCandidates).slice(0, n).map((c, i) => ({ ...c, rank: i + 1 }));
      setBulkResult({
        ...totals,
        avg_score: totals.total_processed ? scoreSum / totals.total_processed : 0,
        total_time_taken_seconds: (performance.now() - started) / 1000,
        semantic_enabled: semanticEnabled,
        warnings: [...warnings],
        top_candidates: top,
        failed_files: failed,
        batches: batches.length,
      });
      setStatus("");
    } catch (e) {
      setError(String(e.message || e));
      setStatus("");
    } finally {
      setLoadingBulk(false);
    }
  }

  return (
    <main className="page">
      <header className="hero">
        <h1>ATS Playground</h1>
        <p>Score and rank resumes against a job: single-resume debug view and bulk screening.</p>
      </header>

      {error && <div className="error">{error}</div>}
      {status && <div className="status">{status}</div>}

      <div className="layout">
        <SectionCard title="Single Resume" subtitle="Run the full pipeline on one resume and inspect the details.">
          <JobForm job={singleJob} setJob={setSingleJob} prefix="single" />
          <label className="upload">
            Resume (.pdf .docx .doc .txt)
            <input type="file" accept=".pdf,.docx,.doc,.txt" onChange={(e) => setSingleFile(e.target.files?.[0] || null)} />
          </label>
          <button onClick={runSingle} disabled={loadingSingle}>
            {loadingSingle ? "Running..." : "Run Single"}
          </button>

          {singleResult && (
            <div className="result">
              <h3>Result</h3>
              <p>
                <strong>Status:</strong> {singleResult.status}
                {singleResult.error_message ? ` - ${singleResult.error_message}` : ""}
              </p>
              {singleResult.status === "processed" && (
                <>
                  <p>
                    <strong>Score:</strong> <Scores s={singleResult.scores} />
                  </p>
                  <p>
                    <strong>Candidate:</strong> {singleResult.name} <small>(from {singleResult.name_source})</small>
                  </p>
                  <p>
                    <strong>Experience:</strong> {singleResult.experience_years} years · <strong>Education:</strong>{" "}
                    {singleResult.education_level}
                    {singleResult.ocr_used ? " · read with OCR" : ""}
                  </p>
                  <p>
                    <strong>Matched:</strong> {(singleResult.matched_skills || []).join(", ") || "none"}
                  </p>
                  <p>
                    <strong>Missing:</strong> {(singleResult.missing_skills || []).join(", ") || "none"}
                  </p>
                  <pre>{singleResult.explanation}</pre>
                </>
              )}
              <details>
                <summary>Raw JSON</summary>
                <pre>{JSON.stringify(singleResult, null, 2)}</pre>
              </details>
            </div>
          )}
        </SectionCard>

        <SectionCard title="Bulk Screening" subtitle="Upload many resumes and view the ranked top candidates.">
          <JobForm job={bulkJob} setJob={setBulkJob} prefix="bulk" />
          <label className="upload">
            Resumes (.pdf .docx .doc .txt) - {bulkFiles.length} selected
            <input
              type="file"
              accept=".pdf,.docx,.doc,.txt"
              multiple
              onChange={(e) => setBulkFiles(Array.from(e.target.files || []))}
            />
          </label>
          <label>
            Top N
            <input type="number" min="1" value={topN} onChange={(e) => setTopN(e.target.value)} />
          </label>
          <button onClick={runBulk} disabled={loadingBulk}>
            {loadingBulk ? "Running..." : "Run Bulk"}
          </button>

          {bulkResult && (
            <div className="result">
              <h3>Summary</h3>
              <p>
                submitted {bulkResult.total_submitted} | ranked {bulkResult.total_processed} | filtered (no required or
                preferred skill found) {bulkResult.total_filtered} | failed {bulkResult.total_failed}
              </p>
              <p>
                <strong>Total time:</strong> {formatSeconds(bulkResult.total_time_taken_seconds)} ·{" "}
                <strong>Average score:</strong> {bulkResult.avg_score.toFixed(1)} · <strong>Semantic scoring:</strong>{" "}
                {bulkResult.semantic_enabled ? "on" : "off"} · {bulkResult.batches} request(s)
              </p>
              {bulkResult.warnings.map((w) => (
                <p key={w} className="warning">
                  {w}
                </p>
              ))}
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Name</th>
                    <th>File</th>
                    <th>Score</th>
                    <th>Exp (yrs)</th>
                    <th>Education</th>
                    <th>Matched skills</th>
                  </tr>
                </thead>
                <tbody>
                  {bulkResult.top_candidates.map((r) => (
                    <tr key={r.candidate_id}>
                      <td>{r.rank}</td>
                      <td>{r.name}</td>
                      <td>
                        {r.file_path}
                        {r.duplicate_of ? ` (duplicate of ${r.duplicate_of})` : ""}
                      </td>
                      <td>
                        <Scores s={r.scores} />
                      </td>
                      <td>{r.experience_years}</td>
                      <td>{r.education_level}</td>
                      <td>
                        {(r.matched_skills || []).join(", ")}
                        <details>
                          <summary>why</summary>
                          <pre>{r.explanation}</pre>
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {bulkResult.failed_files.length > 0 && (
                <>
                  <h3>Files not processed ({bulkResult.failed_files.length})</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>File</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bulkResult.failed_files.map((f) => (
                        <tr key={f.candidate_id}>
                          <td>{f.file_path}</td>
                          <td>{f.error_message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
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
