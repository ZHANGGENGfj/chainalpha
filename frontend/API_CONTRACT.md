# ChainAlpha Frontend API Contract

## POST /api/runs

创建研投任务。

Request:

```json
{
  "topic": "电子布是什么，有没有投资机会",
  "report_type": "industry-chain",
  "depth": "standard",
  "llm_summary": true,
  "chain_visual": true,
  "report_quality_check": true,
  "include_a_share_reports": false,
  "limit": 80,
  "top_k_chunks": 60,
  "top_k_companies": 6
}
```

Response:

```json
{
  "run_id": "20260622_101500",
  "status": "queued"
}
```

## GET /api/runs/{run_id}

查询任务状态。

```json
{
  "run_id": "20260622_101500",
  "topic": "电子布是什么，有没有投资机会",
  "status": "running",
  "stage": "财务验证",
  "started_at": "2026-06-22 10:15:00",
  "elapsed_seconds": 132,
  "output_dir": "data/topic_runs/20260622_101500",
  "metrics": {
    "documents": 8,
    "signals": 2,
    "companies": 6,
    "data_score": 42
  }
}
```

## GET /api/runs/{run_id}/report

读取报告。

Query:

```text
format=markdown | html
```

Response:

```json
{
  "run_id": "20260622_101500",
  "format": "markdown",
  "content": "# 1. 核心结论\n..."
}
```

## GET /api/runs/{run_id}/artifacts

列出产物。

```json
{
  "run_id": "20260622_101500",
  "artifacts": [
    {
      "name": "final_report.md",
      "kind": "markdown",
      "url": "/api/runs/20260622_101500/files/final_report.md"
    },
    {
      "name": "final_report.html",
      "kind": "html",
      "url": "/api/runs/20260622_101500/files/final_report.html"
    },
    {
      "name": "chain_visual.svg",
      "kind": "image",
      "url": "/api/runs/20260622_101500/files/chain_visual.svg"
    }
  ]
}
```
