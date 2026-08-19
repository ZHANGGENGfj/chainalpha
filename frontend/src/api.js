const API_BASE = inferApiBase();

export async function fetchRuns() {
  const data = await requestJson("/api/runs");
  return (data.runs || []).map(toUiRun);
}

export async function fetchSessions() {
  const data = await requestJson("/api/sessions");
  return data.sessions || [];
}

export async function createSession(title = "新研究") {
  return requestJson("/api/sessions", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({title, messages: []})
  });
}

export async function saveSession(session) {
  return requestJson(`/api/sessions/${encodeURIComponent(session.id)}`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      title: session.title || "新研究",
      messages: session.messages || []
    })
  });
}

export async function deleteSession(sessionId) {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE"
  });
}

export async function createRun(form, context = {}) {
  const payload = {
    topic: form.topic,
    // The web workspace currently focuses on the single-company workflow.
    report_type: "company",
    depth: form.depth,
    llm_summary: form.llm,
    chain_visual: form.visual,
    report_quality_check: form.quality,
    include_a_share_reports: form.filings,
    limit: form.maxDocs,
    top_k_chunks: form.topChunks,
    top_k_companies: form.companies,
    session_id: context.sessionId || "",
    assistant_message_id: context.assistantMessageId || ""
  };
  return requestJson("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
}

export async function cancelRun(runId) {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST"
  });
}

export async function fetchReport(runId, format = "markdown") {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/report?format=${format}`);
}

export async function fetchArtifacts(runId) {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/artifacts`);
}

export function parseMarkdownSections(markdown) {
  const normalized = markdown || "";
  let matches = [...normalized.matchAll(/^##?\s+(\d+\.\s+[^\n]+)\n/gm)];
  if (!matches.length) {
    matches = [...normalized.matchAll(/^#{1,3}\s+([^\n]+)\n/gm)];
  }
  if (!matches.length) {
    return [
      {
        title: "报告正文",
        body: normalized
          .split(/\n{2,}/)
          .map((item) => item.trim())
          .filter(Boolean)
          .slice(0, 24)
      }
    ];
  }
  return matches.map((match, index) => {
    const start = match.index + match[0].length;
    const end = matches[index + 1]?.index ?? normalized.length;
    return {
      title: match[1].trim(),
      body: normalized
        .slice(start, end)
        .split(/\n{2,}/)
        .map((item) => item.replace(/^[-*]\s+/gm, "").trim())
        .filter(Boolean)
        .slice(0, 16)
    };
  });
}

function toUiRun(item) {
  const metrics = item.metrics || {};
  const request = item.request || {};
  const artifacts = item.artifacts || [];
  const status = item.status || "unknown";
  return {
    id: item.run_id,
    topic: item.topic || item.run_id,
    type: reportTypeLabel(request.report_type),
    status,
    generatedAt: item.started_at || "",
    duration: status === "running" ? "运行中" : "",
    outputDir: item.output_dir || "",
    docs: metrics.documents || 0,
    signals: metrics.signals || 0,
    companies: item.companies || [],
    dataScore: metrics.data_score || 0,
    risk: metrics.data_score >= 70 ? "中" : "中高",
    currentStage: item.stage || status,
    artifacts,
    evidence: [
      `输出目录：${item.output_dir || "-"}`,
      `任务状态：${status}`,
      `当前阶段：${item.stage || "-"}`
    ],
    gaps: artifacts.includes("report_quality_audit.json")
      ? ["质量检查已生成，可查看审计产物。"]
      : ["未发现质量检查产物，建议启用报告检查。"]
  };
}

async function requestJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {...options, credentials: "include"});
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function inferApiBase() {
  if (window.CHAINALPHA_API_BASE) {
    return window.CHAINALPHA_API_BASE;
  }
  if (location.port === "5173" || location.port === "5174") {
    return "http://127.0.0.1:8000";
  }
  return "";
}

function reportTypeLabel(value) {
  if (value === "company") return "单公司深度";
  if (value === "industry-chain") return "产业链瓶颈";
  if (value === "daily") return "每日扫描";
  return "主题研究";
}
