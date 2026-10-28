import { Fragment, useState, useEffect } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const CLIENT_HEADERS = { "X-ATS-Client": "webui" };
const BATCH_SIZE = 900;

const defaultJob = {
  title: "Senior Backend Engineer",
  description:
    "We need engineers with strong Python backend experience, REST API design, containers, cloud, and production data systems.",
  required_skills: "python,sql,docker,kubernetes,fastapi",
  preferred_skills: "redis,linux,terraform,kafka",
  min_experience_years: "3",
  required_education: "bachelor",
  reference_date: "",
};

const JOB_PRESETS = {
  python: {
    title: "Senior Backend Engineer",
    description: "Hands-on experience developing Python APIs, microservices, PostgreSQL, Docker, and AWS.",
    required_skills: "python,fastapi,sql,docker,postgresql",
    preferred_skills: "redis,aws,kubernetes,git",
    min_experience_years: "3",
    required_education: "bachelor",
  },
  fullstack: {
    title: "Full Stack Developer",
    description: "Build modern web apps using React, Node.js or Python, TypeScript, and cloud deployment.",
    required_skills: "react,javascript,typescript,python,html,css",
    preferred_skills: "node.js,next.js,docker,tailwind",
    min_experience_years: "2",
    required_education: "bachelor",
  },
  datascience: {
    title: "Data Scientist / ML Engineer",
    description: "Build, deploy, and evaluate machine learning models with Python, Pandas, Scikit-Learn, and PyTorch.",
    required_skills: "python,machine learning,pandas,numpy,sql",
    preferred_skills: "pytorch,tensorflow,scikit-learn,nlp",
    min_experience_years: "2",
    required_education: "bachelor",
  },
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

function formatSeconds(seconds) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value < 0) return "0.0s";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${(value - minutes * 60).toFixed(1)}s`;
}

function ScoreBadge({ score }) {
  const val = Number(score || 0);
  let cls = "score-low";
  if (val >= 60) cls = "score-high";
  else if (val >= 35) cls = "score-mid";
  return <span className={`score-badge ${cls}`}>{val.toFixed(1)}</span>;
}

export default function App() {
  const [activeTab, setActiveTab] = useState("ats");
  const [apiHealth, setApiHealth] = useState({ status: "checking", ocr: false });

  // ATS State
  const [atsMode, setAtsMode] = useState("bulk");
  const [bulkJob, setBulkJob] = useState(defaultJob);
  const [bulkFiles, setBulkFiles] = useState([]);
  const [topN, setTopN] = useState("10");
  const [loadingBulk, setLoadingBulk] = useState(false);
  const [bulkResult, setBulkResult] = useState(null);
  const [bulkProgress, setBulkProgress] = useState(null);
  const [openWhy, setOpenWhy] = useState(new Set());

  // Single Resume State
  const [singleJob, setSingleJob] = useState(defaultJob);
  const [singleFile, setSingleFile] = useState(null);
  const [loadingSingle, setLoadingSingle] = useState(false);
  const [singleResult, setSingleResult] = useState(null);

  // Resume Extractor State
  const [extractFiles, setExtractFiles] = useState([]);
  const [loadingExtract, setLoadingExtract] = useState(false);
  const [extractRecords, setExtractRecords] = useState([]);
  const [extractSearch, setExtractSearch] = useState("");
  const [extractFolder, setExtractFolder] = useState("");
  const [downloadingExcel, setDownloadingExcel] = useState(false);
  const [extractSuccessMsg, setExtractSuccessMsg] = useState("");
  const [extractProgress, setExtractProgress] = useState(null);

  // Check Health on Mount
  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => r.json())
      .then((d) => setApiHealth({ status: "ok", ocr: d.ocr_available, semantic: d.semantic_bulk }))
      .catch(() => setApiHealth({ status: "offline", ocr: false }));
  }, []);

  // ---------------- ATS Single Run ----------------
  async function runSingle() {
    if (!singleFile) return alert("Please select a resume file (.pdf, .docx, .doc, .txt)");
    setLoadingSingle(true);
    setSingleResult(null);
    try {
      const fd = buildForm(singleJob);
      fd.append("resume", singleFile);
      const res = await fetch(`${API_BASE}/api/single`, { method: "POST", headers: CLIENT_HEADERS, body: fd });
      if (!res.ok) throw new Error(await readError(res));
      setSingleResult(await res.json());
    } catch (err) {
      alert(`Screening failed: ${err.message}`);
    } finally {
      setLoadingSingle(false);
    }
  }

  // ---------------- ATS Bulk Run ----------------
  async function runBulk() {
    if (!bulkFiles.length) return alert("Please select resume files or a .zip archive");
    setLoadingBulk(true);
    setBulkResult(null);
    setBulkProgress(null);
    setOpenWhy(new Set());

    const chunks = [];
    for (let i = 0; i < bulkFiles.length; i += BATCH_SIZE) {
      chunks.push(bulkFiles.slice(i, i + BATCH_SIZE));
    }

    const aggregated = {
      total_submitted: bulkFiles.length,
      total_processed: 0,
      total_filtered: 0,
      total_failed: 0,
      total_time_taken_seconds: 0,
      top_candidates: [],
      failed_files: [],
      batches: chunks.length,
      semantic_enabled: true,
      avg_score: 0,
      warnings: [],
    };

    try {
      for (let b = 0; b < chunks.length; b++) {
        const chunk = chunks[b];
        const reqId = newRequestId();
        const fd = buildForm(bulkJob, { top_n: topN, request_id: reqId });
        chunk.forEach((f) => fd.append("resumes", f));

        const timer = setInterval(async () => {
          try {
            const pr = await fetch(`${API_BASE}/api/progress/${reqId}`);
            if (pr.ok) {
              const pdata = await pr.json();
              setBulkProgress({ batch: b + 1, totalBatches: chunks.length, ...pdata });
            }
          } catch {
            /* ignore polling error */
          }
        }, 1500);

        let data;
        try {
          const res = await fetch(`${API_BASE}/api/bulk`, { method: "POST", headers: CLIENT_HEADERS, body: fd });
          if (!res.ok) throw new Error(await readError(res));
          data = await res.json();
        } finally {
          clearInterval(timer);
        }

        aggregated.total_processed += data.total_processed || 0;
        aggregated.total_filtered += data.total_filtered || 0;
        aggregated.total_failed += data.total_failed || 0;
        aggregated.total_time_taken_seconds += data.time_taken_seconds || 0;
        aggregated.semantic_enabled = data.summary_stats?.semantic_enabled ?? aggregated.semantic_enabled;
        aggregated.top_candidates.push(...(data.top_candidates || []));
        aggregated.failed_files.push(...(data.failed_files || []));
        if (data.warnings) aggregated.warnings.push(...data.warnings);
      }

      aggregated.top_candidates.sort(rankCandidates);
      const topCount = parseInt(topN, 10) || 10;
      aggregated.top_candidates = aggregated.top_candidates.slice(0, topCount);
      aggregated.top_candidates.forEach((c, idx) => {
        c.rank = idx + 1;
      });

      const scores = aggregated.top_candidates.map((c) => c.scores?.final || 0);
      aggregated.avg_score = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;
      setBulkResult(aggregated);
    } catch (err) {
      alert(`Bulk screening failed: ${err.message}`);
    } finally {
      setLoadingBulk(false);
      setBulkProgress(null);
    }
  }

  // ---------------- Resume Extractor Run ----------------
  async function runExtractor() {
    if (!extractFiles.length) return alert("Please select resume files or a .zip archive to extract.");
    setLoadingExtract(true);
    setExtractRecords([]);
    setExtractSuccessMsg("");
    setExtractProgress({ stage: "uploading", analyzed: 0, total: extractFiles.length });
    const reqId = newRequestId();
    const startTime = performance.now();

    const timer = setInterval(async () => {
      try {
        const pr = await fetch(`${API_BASE}/api/progress/${reqId}`);
        if (pr.ok) {
          const pdata = await pr.json();
          setExtractProgress(pdata);
        }
      } catch {
        /* ignore */
      }
    }, 800);

    try {
      const fd = new FormData();
      fd.append("request_id", reqId);
      extractFiles.forEach((f) => fd.append("files", f));
      const res = await fetch(`${API_BASE}/api/extract`, {
        method: "POST",
        headers: CLIENT_HEADERS,
        body: fd,
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      const records = data.records || [];
      setExtractRecords(records);
      const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);
      setExtractSuccessMsg(`Extracted ${records.length} candidates in ${elapsed} seconds.`);
    } catch (err) {
      alert(`Extraction failed: ${err.message}`);
    } finally {
      clearInterval(timer);
      setLoadingExtract(false);
      setExtractProgress(null);
    }
  }

  // ---------------- Direct Extract & Download Excel ----------------
  async function runExtractAndDownloadDirect() {
    if (!extractFiles.length) return alert("Please select resume files or a .zip archive to download Excel.");
    setDownloadingExcel(true);
    setExtractSuccessMsg("");
    setExtractProgress({ stage: "uploading", analyzed: 0, total: extractFiles.length });
    const reqId = newRequestId();
    const startTime = performance.now();

    const timer = setInterval(async () => {
      try {
        const pr = await fetch(`${API_BASE}/api/progress/${reqId}`);
        if (pr.ok) {
          const pdata = await pr.json();
          setExtractProgress(pdata);
        }
      } catch {
        /* ignore */
      }
    }, 800);

    try {
      const fd = new FormData();
      fd.append("request_id", reqId);
      extractFiles.forEach((f) => fd.append("files", f));
      const res = await fetch(`${API_BASE}/api/extract/excel`, {
        method: "POST",
        headers: CLIENT_HEADERS,
        body: fd,
      });
      if (!res.ok) throw new Error(await readError(res));
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `extracted_resumes_${Date.now()}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);
      setExtractSuccessMsg(`Excel file generated and downloaded in ${elapsed} seconds.`);
    } catch (err) {
      alert(`Download failed: ${err.message}`);
    } finally {
      clearInterval(timer);
      setDownloadingExcel(false);
      setExtractProgress(null);
    }
  }

  // ---------------- Extract Directly from Local Folder ----------------
  async function runExtractFolder() {
    if (!extractFolder.trim()) return alert("Please enter a valid local folder path on this machine.");
    setLoadingExtract(true);
    setExtractRecords([]);
    setExtractSuccessMsg("");
    setExtractProgress({ stage: "scanning_folder", analyzed: 0, total: 0 });
    const reqId = newRequestId();
    const startTime = performance.now();

    const timer = setInterval(async () => {
      try {
        const pr = await fetch(`${API_BASE}/api/progress/${reqId}`);
        if (pr.ok) {
          const pdata = await pr.json();
          setExtractProgress(pdata);
        }
      } catch {
        /* ignore */
      }
    }, 800);

    try {
      const fd = new FormData();
      fd.append("request_id", reqId);
      fd.append("folder_path", extractFolder.trim());
      const res = await fetch(`${API_BASE}/api/extract-folder`, {
        method: "POST",
        headers: CLIENT_HEADERS,
        body: fd,
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      const records = data.records || [];
      setExtractRecords(records);
      const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);
      setExtractSuccessMsg(`Extracted ${records.length} candidates from folder in ${elapsed} seconds.`);
    } catch (err) {
      alert(`Folder extraction failed: ${err.message}`);
    } finally {
      clearInterval(timer);
      setLoadingExtract(false);
      setExtractProgress(null);
    }
  }

  // ---------------- Download Excel Directly ----------------
  async function downloadExcel(recordsToDownload) {
    const list = recordsToDownload || extractRecords;
    if (!list || !list.length) return alert("No extracted records to download.");
    setDownloadingExcel(true);
    try {
      const res = await fetch(`${API_BASE}/api/extract/download-excel`, {
        method: "POST",
        headers: { ...CLIENT_HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify({ records: list }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `extracted_resumes_${Date.now()}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert(`Download failed: ${err.message}`);
    } finally {
      setDownloadingExcel(false);
    }
  }

  // Filtered extraction records based on search input
  const filteredExtractRecords = extractRecords.filter((r) => {
    if (!extractSearch.trim()) return true;
    const q = extractSearch.toLowerCase();
    return (
      (r["Candidate Name"] || "").toLowerCase().includes(q) ||
      (r["Skills"] || "").toLowerCase().includes(q) ||
      (r["Current Location"] || "").toLowerCase().includes(q) ||
      (r["Current Company"] || "").toLowerCase().includes(q) ||
      (r["Email"] || "").toLowerCase().includes(q) ||
      (r["File Name"] || "").toLowerCase().includes(q)
    );
  });

  return (
    <div className="page fade-in">
      {/* Header Bar */}
      <header className="header-bar">
        <div className="brand-wrapper">
          <div className="brand-icon">TS</div>
          <div>
            <h1 className="brand-title">TalentAI Suite</h1>
            <p className="brand-subtitle">ATS Resume Screening & Bulk Candidate Data Extractor</p>
          </div>
        </div>
      </header>

      {/* Tabs Navigation: Two Clean Standalone Modules */}
      <nav className="tabs-nav">
        <button
          id="tab-ats-btn"
          className={`tab-btn ${activeTab === "ats" ? "active" : ""}`}
          onClick={() => setActiveTab("ats")}
        >
          ATS Candidate Screening
          <span className="tab-badge">Top N Rank</span>
        </button>
        <button
          id="tab-extractor-btn"
          className={`tab-btn ${activeTab === "extractor" ? "active" : ""}`}
          onClick={() => setActiveTab("extractor")}
        >
          Resume Data Extractor
          <span className="tab-badge">Excel Export</span>
        </button>
      </nav>

      {/* ========================================================================= */}
      {/* TAB 1: ATS SCREENING */}
      {/* ========================================================================= */}
      {activeTab === "ats" && (
        <div className="fade-in">
          {/* Submode Switcher */}
          <div style={{ display: "flex", gap: "10px", marginBottom: "18px" }}>
            <button
              className={`btn ${atsMode === "bulk" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setAtsMode("bulk")}
            >
              Bulk Screening (Multi-file & ZIP)
            </button>
            <button
              className={`btn ${atsMode === "single" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setAtsMode("single")}
            >
              Single Quick Screen
            </button>
          </div>

          {/* Job Configuration Card */}
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Job Requirements & Evaluation Criteria</h2>
                <p className="card-subtitle">Define target role attributes for semantic AI scoring and skills matching</p>
              </div>
              {/* Presets */}
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px", fontSize: "0.8rem" }}
                  onClick={() => (atsMode === "bulk" ? setBulkJob(JOB_PRESETS.python) : setSingleJob(JOB_PRESETS.python))}
                >
                  Preset: Python Backend
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px", fontSize: "0.8rem" }}
                  onClick={() => (atsMode === "bulk" ? setBulkJob(JOB_PRESETS.fullstack) : setSingleJob(JOB_PRESETS.fullstack))}
                >
                  Preset: Full Stack
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px", fontSize: "0.8rem" }}
                  onClick={() => (atsMode === "bulk" ? setBulkJob(JOB_PRESETS.datascience) : setSingleJob(JOB_PRESETS.datascience))}
                >
                  Preset: Data Science
                </button>
              </div>
            </div>

            <div className="grid two">
              <label>
                Role Title
                <input
                  value={atsMode === "bulk" ? bulkJob.title : singleJob.title}
                  onChange={(e) =>
                    atsMode === "bulk"
                      ? setBulkJob({ ...bulkJob, title: e.target.value })
                      : setSingleJob({ ...singleJob, title: e.target.value })
                  }
                />
              </label>
              <label>
                Required Education
                <select
                  value={atsMode === "bulk" ? bulkJob.required_education : singleJob.required_education}
                  onChange={(e) =>
                    atsMode === "bulk"
                      ? setBulkJob({ ...bulkJob, required_education: e.target.value })
                      : setSingleJob({ ...singleJob, required_education: e.target.value })
                  }
                >
                  <option value="any">Any Degree</option>
                  <option value="diploma">Diploma</option>
                  <option value="bachelor">Bachelor&apos;s Degree</option>
                  <option value="master">Master&apos;s Degree</option>
                  <option value="phd">Ph.D.</option>
                </select>
              </label>
              <label className="span2">
                Job Description
                <textarea
                  rows={3}
                  value={atsMode === "bulk" ? bulkJob.description : singleJob.description}
                  onChange={(e) =>
                    atsMode === "bulk"
                      ? setBulkJob({ ...bulkJob, description: e.target.value })
                      : setSingleJob({ ...singleJob, description: e.target.value })
                  }
                />
              </label>
              <label>
                Required Skills (comma-separated)
                <input
                  value={atsMode === "bulk" ? bulkJob.required_skills : singleJob.required_skills}
                  onChange={(e) =>
                    atsMode === "bulk"
                      ? setBulkJob({ ...bulkJob, required_skills: e.target.value })
                      : setSingleJob({ ...singleJob, required_skills: e.target.value })
                  }
                />
              </label>
              <label>
                Preferred Skills (comma-separated)
                <input
                  value={atsMode === "bulk" ? bulkJob.preferred_skills : singleJob.preferred_skills}
                  onChange={(e) =>
                    atsMode === "bulk"
                      ? setBulkJob({ ...bulkJob, preferred_skills: e.target.value })
                      : setSingleJob({ ...singleJob, preferred_skills: e.target.value })
                  }
                />
              </label>
              <label>
                Minimum Experience (Years)
                <input
                  type="number"
                  min="0"
                  value={atsMode === "bulk" ? bulkJob.min_experience_years : singleJob.min_experience_years}
                  onChange={(e) =>
                    atsMode === "bulk"
                      ? setBulkJob({ ...bulkJob, min_experience_years: e.target.value })
                      : setSingleJob({ ...singleJob, min_experience_years: e.target.value })
                  }
                />
              </label>
              {atsMode === "bulk" && (
                <label>
                  Top Candidates to Rank (Top N)
                  <input type="number" min="1" max="1000" value={topN} onChange={(e) => setTopN(e.target.value)} />
                </label>
              )}
            </div>
          </section>

          {/* Upload Section */}
          <section className="card">
            <h2 className="card-title">{atsMode === "bulk" ? "Upload Candidate Resumes" : "Upload Single Resume"}</h2>
            <p className="card-subtitle">
              Supports PDF, DOCX, DOC, TXT, and complete ZIP archives up to 500 MB (with automatic streaming extraction)
            </p>

            <div className="upload-zone" style={{ marginTop: "16px" }}>
              <p className="upload-zone-text">
                {atsMode === "bulk"
                  ? bulkFiles.length > 0
                    ? `Selected ${bulkFiles.length} file(s)`
                    : "Drag & drop files or click to browse"
                  : singleFile
                  ? `Selected: ${singleFile.name}`
                  : "Click to select a resume"}
              </p>
              <span className="upload-zone-hint">PDF, DOCX, DOC, TXT, ZIP up to 500 MB</span>
              <input
                type="file"
                multiple={atsMode === "bulk"}
                accept=".pdf,.docx,.doc,.txt,.zip"
                onChange={(e) => {
                  if (atsMode === "bulk") {
                    setBulkFiles(Array.from(e.target.files || []));
                  } else {
                    setSingleFile(e.target.files?.[0] || null);
                  }
                }}
              />
            </div>

            {/* Action Bar */}
            <div style={{ marginTop: "18px", display: "flex", gap: "12px", alignItems: "center" }}>
              {atsMode === "bulk" ? (
                <button className="btn btn-primary" onClick={runBulk} disabled={loadingBulk || !bulkFiles.length}>
                  {loadingBulk ? "Screening in Progress..." : `Run Bulk Screening (${bulkFiles.length} files)`}
                </button>
              ) : (
                <button className="btn btn-primary" onClick={runSingle} disabled={loadingSingle || !singleFile}>
                  {loadingSingle ? "Screening..." : "Run Single Screening"}
                </button>
              )}
            </div>

            {/* Progress Bar */}
            {bulkProgress && (
              <div style={{ marginTop: "18px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", color: "var(--text-muted)" }}>
                  <span>
                    Batch {bulkProgress.batch} of {bulkProgress.totalBatches} · Stage:{" "}
                    <strong style={{ color: "#ffffff", textTransform: "capitalize" }}>
                      {bulkProgress.stage || "processing"}
                    </strong>
                  </span>
                  <span>
                    {bulkProgress.analyzed ?? bulkProgress.processed_files ?? 0} /{" "}
                    {bulkProgress.total ?? bulkProgress.total_files ?? "calculating..."} files
                  </span>
                </div>
                <div className="progress-bar-container">
                  <div
                    className="progress-bar-fill"
                    style={{
                      width: `${
                        (bulkProgress.total || bulkProgress.total_files)
                          ? Math.min(100, Math.max(5, Math.round((((bulkProgress.analyzed || bulkProgress.processed_files || 0) / (bulkProgress.total || bulkProgress.total_files)) * 100))))
                          : (bulkProgress.stage === "uploading" ? 20 : 60)
                      }%`,
                    }}
                  ></div>
                </div>
              </div>
            )}
          </section>

          {/* Single Result Display */}
          {atsMode === "single" && singleResult && (
            <section className="card">
              <h2 className="card-title">Screening Result</h2>
              <div className="stats-row" style={{ marginTop: "16px" }}>
                <div className="stat-card">
                  <div className="stat-label">Overall Match Score</div>
                  <div className="stat-value">{singleResult.scores?.final?.toFixed(1) || 0}%</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Candidate Name</div>
                  <div className="stat-value" style={{ fontSize: "1.3rem" }}>
                    {singleResult.name || "Candidate"}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Experience</div>
                  <div className="stat-value">{singleResult.experience_years || 0} Yrs</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Education Match</div>
                  <div className="stat-value" style={{ fontSize: "1.2rem" }}>
                    {singleResult.education_level || "—"}
                  </div>
                </div>
              </div>

              <div style={{ marginTop: "16px" }}>
                <h3 style={{ fontSize: "1rem", marginBottom: "8px" }}>Matched Skills:</h3>
                {(singleResult.matched_skills || []).map((s) => (
                  <span key={s} className="pill pill-success">
                    {s}
                  </span>
                ))}
              </div>

              {singleResult.missing_skills?.length > 0 && (
                <div style={{ marginTop: "12px" }}>
                  <h3 style={{ fontSize: "1rem", marginBottom: "8px" }}>Missing Required Skills:</h3>
                  {singleResult.missing_skills.map((s) => (
                    <span key={s} className="pill pill-danger">
                      {s}
                    </span>
                  ))}
                </div>
              )}

              {singleResult.explanation && (
                <div style={{ marginTop: "18px" }}>
                  <h3 style={{ fontSize: "1rem", marginBottom: "6px" }}>Detailed Scoring Rationale:</h3>
                  <div className="why-box">{singleResult.explanation}</div>
                </div>
              )}
            </section>
          )}

          {/* Bulk Results Table */}
          {atsMode === "bulk" && bulkResult && (
            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">Screening Ranking (Top Candidates)</h2>
                  <p className="card-subtitle">
                    Processed {bulkResult.total_processed} · Filtered {bulkResult.total_filtered} · Failed{" "}
                    {bulkResult.total_failed} · Time {formatSeconds(bulkResult.total_time_taken_seconds)}
                  </p>
                </div>
              </div>

              <div className="stats-row">
                <div className="stat-card">
                  <div className="stat-label">Top Ranked Candidates</div>
                  <div className="stat-value">{bulkResult.top_candidates.length}</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Average Score</div>
                  <div className="stat-value">{bulkResult.avg_score.toFixed(1)}%</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Filtered Out</div>
                  <div className="stat-value">{bulkResult.total_filtered}</div>
                </div>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: "60px" }}>Rank</th>
                      <th>Candidate Name</th>
                      <th>Source File</th>
                      <th>Score</th>
                      <th>Experience</th>
                      <th>Education</th>
                      <th>Matched Skills</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bulkResult.top_candidates.map((c) => (
                      <Fragment key={c.candidate_id}>
                        <tr>
                          <td>
                            <strong>#{c.rank}</strong>
                          </td>
                          <td style={{ fontWeight: 600, color: "#fff" }}>{c.name}</td>
                          <td style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>{c.file_path}</td>
                          <td>
                            <ScoreBadge score={c.scores?.final} />
                          </td>
                          <td>{c.experience_years} yrs</td>
                          <td>{c.education_level}</td>
                          <td>
                            {(c.matched_skills || []).slice(0, 4).map((s) => (
                              <span key={s} className="pill pill-success">
                                {s}
                              </span>
                            ))}
                            {(c.matched_skills || []).length > 4 && (
                              <span className="pill">+{c.matched_skills.length - 4} more</span>
                            )}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-secondary"
                              style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                              onClick={() => {
                                setOpenWhy((prev) => {
                                  const n = new Set(prev);
                                  n.has(c.candidate_id) ? n.delete(c.candidate_id) : n.add(c.candidate_id);
                                  return n;
                                });
                              }}
                            >
                              {openWhy.has(c.candidate_id) ? "Hide Details" : "View Why"}
                            </button>
                          </td>
                        </tr>
                        {openWhy.has(c.candidate_id) && (
                          <tr>
                            <td colSpan={8} style={{ padding: "0 16px 16px" }}>
                              <div className="why-box">{c.explanation}</div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 2: RESUME DATA EXTRACTOR */}
      {/* ========================================================================= */}
      {activeTab === "extractor" && (
        <div className="fade-in">
          {/* Upload Card */}
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Bulk Resume Data Extraction Engine</h2>
                <p className="card-subtitle">
                  Deep multi-engine extractor for contact info, total experience, skills, current & previous companies, location, and education
                </p>
              </div>
            </div>

            {extractSuccessMsg && (
              <div className="status-banner">
                <span>{extractSuccessMsg}</span>
              </div>
            )}

            <div className="upload-zone">
              <p className="upload-zone-text">
                {extractFiles.length > 0
                  ? `Selected ${extractFiles.length} file(s) · ${extractFiles.map((f) => f.name).slice(0, 3).join(", ")}${extractFiles.length > 3 ? "..." : ""}`
                  : "Drag & Drop ZIP file or candidate resumes here"}
              </p>
              <span className="upload-zone-hint">Accepts ZIP archive (up to 500 MB), PDF, DOCX, DOC, or TXT</span>
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.doc,.txt,.zip"
                onChange={(e) => {
                  setExtractSuccessMsg("");
                  setExtractFiles(Array.from(e.target.files || []));
                }}
              />
            </div>

            {/* Live Progress Bar for Extractor */}
            {extractProgress && (
              <div
                style={{
                  marginTop: "16px",
                  padding: "16px 20px",
                  background: "#111111",
                  border: "1px solid var(--border-light)",
                  borderRadius: "6px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginBottom: "8px",
                    fontSize: "0.9rem",
                    fontWeight: 600,
                  }}
                >
                  <span style={{ color: "#ffffff" }}>
                    {extractProgress.stage === "uploading"
                      ? "Uploading & unpacking archive..."
                      : extractProgress.stage === "extracting"
                      ? `Extracting candidate profiles: ${extractProgress.analyzed || 0} / ${extractProgress.total || "..."} resumes`
                      : extractProgress.stage === "formatting_excel"
                      ? "Formatting Excel workbook..."
                      : extractProgress.stage === "scanning_folder"
                      ? "Scanning local folder for resumes..."
                      : `Processing (${extractProgress.stage || "in progress"})...`}
                  </span>
                  <span style={{ color: "#a3a3a3" }}>
                    {extractProgress.total > 0
                      ? `${Math.round(((extractProgress.analyzed || 0) / extractProgress.total) * 100)}%`
                      : ""}
                  </span>
                </div>
                <div className="progress-bar-container">
                  <div
                    className="progress-bar-fill"
                    style={{
                      width: `${
                        extractProgress.total > 0
                          ? Math.min(
                              100,
                              Math.round(((extractProgress.analyzed || 0) / extractProgress.total) * 100)
                            )
                          : 15
                      }%`,
                    }}
                  />
                </div>
              </div>
            )}

            <div style={{ marginTop: "18px", display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
              <button
                className="btn btn-primary"
                onClick={runExtractAndDownloadDirect}
                disabled={downloadingExcel || !extractFiles.length}
                style={{ padding: "12px 24px", fontSize: "0.95rem", fontWeight: "600" }}
              >
                {downloadingExcel ? "Generating Excel..." : "Process Resumes & Download Excel (.xlsx)"}
              </button>
              <button
                className="btn btn-secondary"
                onClick={runExtractor}
                disabled={loadingExtract || !extractFiles.length}
              >
                {loadingExtract ? "Extracting Data..." : `Extract & Preview Table (${extractFiles.length} files)`}
              </button>
              {extractRecords.length > 0 && (
                <button
                  className="btn btn-secondary"
                  onClick={() => downloadExcel(filteredExtractRecords)}
                  disabled={downloadingExcel}
                >
                  Export Current Table to Excel
                </button>
              )}
            </div>

            {/* Local Folder Extraction Option */}
            <div style={{ marginTop: "24px", paddingTop: "18px", borderTop: "1px solid var(--border-light)" }}>
              <p style={{ fontSize: "0.84rem", fontWeight: 600, color: "var(--text-muted)", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                Or Extract Directly from a Local Directory on this Machine:
              </p>
              <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
                <input
                  style={{ flex: 1, minWidth: "260px" }}
                  type="text"
                  placeholder="e.g. D:\ayyappa\sample_resumes or any resume directory"
                  value={extractFolder}
                  onChange={(e) => setExtractFolder(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={runExtractFolder}
                  disabled={loadingExtract || !extractFolder.trim()}
                >
                  {loadingExtract ? "Extracting Folder..." : "Extract Local Folder"}
                </button>
              </div>
            </div>
          </section>

          {/* Results Grid */}
          {extractRecords.length > 0 && (
            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">
                    Extracted Candidates ({filteredExtractRecords.length} of {extractRecords.length})
                  </h2>
                  <p className="card-subtitle">Interactive data view with instant search and Excel export</p>
                </div>
                <div className="search-box">
                  <input
                    type="text"
                    placeholder="Search candidate, skill, location, company..."
                    value={extractSearch}
                    onChange={(e) => setExtractSearch(e.target.value)}
                  />
                </div>
              </div>

              {/* Statistics Cards */}
              <div className="stats-row">
                <div className="stat-card">
                  <div className="stat-label">Total Extracted</div>
                  <div className="stat-value">{extractRecords.length}</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">With Verified Phone</div>
                  <div className="stat-value">
                    {extractRecords.filter((r) => r["Candidate Contact Number"] && r["Candidate Contact Number"] !== "Not Provided").length}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">With Verified Email</div>
                  <div className="stat-value">
                    {extractRecords.filter((r) => r["Email"] && r["Email"] !== "Not Provided").length}
                  </div>
                </div>
              </div>

              {/* Data Table */}
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Candidate Name</th>
                      <th>Contact Phone</th>
                      <th>Email</th>
                      <th>Experience</th>
                      <th>Current Company</th>
                      <th>Location</th>
                      <th>Education</th>
                      <th>Skills</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredExtractRecords.map((row, idx) => (
                      <tr key={idx}>
                        <td>{idx + 1}</td>
                        <td style={{ fontWeight: 600, color: "#fff", whiteSpace: "nowrap" }}>
                          {row["Candidate Name"]}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }}>{row["Candidate Contact Number"]}</td>
                        <td>{row["Email"]}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{row["Total Years of Experience"]}</td>
                        <td>{row["Current Company"]}</td>
                        <td>{row["Current Location"]}</td>
                        <td>{row["Education (UG / PG Degree)"]}</td>
                        <td style={{ maxWidth: "260px" }}>
                          {(row["Skills"] || "")
                            .split(",")
                            .slice(0, 3)
                            .map((s, si) => (
                              <span key={si} className="pill">
                                {s.trim()}
                              </span>
                            ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
