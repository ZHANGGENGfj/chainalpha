# ChainAlpha MVP

**ChainAlpha** 是一个面向产业链研究的开源弱信号智能体 MVP。  
它不是新闻情绪选股机器人，而是把公开信息、Obsidian/Markdown 知识库、产业链图谱、事件抽取、因果传导和弱信号评分串起来，用于发现“尚未成为热点但产业逻辑已经开始变化”的信号。

> 默认示例聚焦 AI 产业链：AI CapEx、GPU/HBM/CoWoS、数据中心、电力、液冷、光模块、硅光、LPO/CPO、企业 AI 应用等。

## 核心功能

- 读取 Obsidian Vault / Markdown 文件夹
- 数据源连接层：local_files / obsidian / MediaCrawler 输出 / RSS / 固定网页
- 解析 frontmatter、标题、标签、双链 `[[...]]`
- 构建轻量混合检索：关键词 BM25-like + TF-IDF cosine
- 基于规则抽取产业事件；可选 OpenAI-compatible LLM 增强
- AI 产业链图谱映射与多跳传导
- 约束迁移识别与弱信号评分
- 生成 Markdown 研究报告
- 支持 CLI、API、后续接入 MediaCrawler-main

## 快速开始

```bash
cd chainalpha_mvp
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
# source .venv/bin/activate

pip install -e .
```

初始化一个 Obsidian 兼容知识库：

```bash
chainalpha init-vault --path ./vault
```

运行 AI 产业链弱信号扫描：

```bash
chainalpha scan --vault ./vault --out ./outputs/reports
```

查看知识库检索结果：

```bash
chainalpha ask --vault ./vault --query "光模块行情为什么来自 AI 集群网络瓶颈？"
```

预览配置的数据源连接器：

```bash
chainalpha fetch-sources --config ./configs/sources.yaml --query "光模块 HBM 液冷"
```

从配置的数据源抓取内容并扫描弱信号：

```bash
chainalpha scan-sources --config ./configs/sources.yaml --out ./outputs/reports
```

启动 API：

```bash
uvicorn chainalpha.api.app:app --reload --port 8000
```


## 数据源连接层

`configs/sources.yaml` 控制外部数据接入。当前已经实现：

| 类型 | 说明 |
|---|---|
| `local_files` | 读取本地 `.md`、`.txt`、`.json`、`.jsonl`、`.csv` 文件 |
| `obsidian` | 读取 Obsidian 兼容 Markdown Vault |
| `media_crawler` | 读取已有 `MediaCrawler-main/data` 输出，不直接启动浏览器爬虫 |
| `rss` | 读取 RSS/Atom 订阅源 |
| `web_pages` | 读取固定网页，例如公司公告、产品发布页、官方新闻页 |

连接器只负责把不同来源统一成 `SourceDocument`，不会做事件推理和评分。事件抽取、产业链映射、弱信号评分在后续 pipeline 中完成。

示例配置：

```yaml
sources:
  - name: local_raw_files
    type: local_files
    enabled: true
    path: ./data/raw
    patterns: ["*.md", "*.txt", "*.json", "*.jsonl", "*.csv"]

  - name: media_crawler_outputs
    type: media_crawler
    enabled: false
    root_dir: ../MediaCrawler-main/data
    platforms: [xq, cls]
    modes: [cache, processed, raw]
    keywords: [AI产业链, 光模块, HBM, CoWoS, 液冷]
```

## 可选：接入大模型

`.env.example` 中提供 OpenAI-compatible 配置，例如 DashScope/Qwen、DeepSeek、OpenAI、本地 vLLM/Ollama 兼容接口。

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export OPENAI_MODEL="qwen-plus"
```

没有 API key 时，系统会使用规则抽取器和本地检索，仍然可以运行。

## 目录结构

```text
chainalpha_mvp/
├── src/chainalpha/
│   ├── cli.py
│   ├── schemas.py
│   ├── config.py
│   ├── connectors/        # 数据源连接层
│   ├── memory/
│   ├── nlp/
│   ├── ontology/
│   ├── reasoning/
│   ├── scoring/
│   ├── reports/
│   ├── api/
│   └── utils/
├── examples/
├── configs/
└── tests/
```

## 免责声明

本项目仅用于产业链研究、信息整理和弱信号分析，不构成投资建议，不提供自动交易能力。

## Full data-source connectors

`configs/sources_full.yaml` contains templates for the source classes below. Most external sources are disabled by default. Enable only the ones you have permission to use.

### Supported connector types

| Type | Purpose |
|---|---|
| `news_source` | Predefined news/search adapters for 财联社、证券时报、36氪、21世纪经济报道、Reuters、Bloomberg、PRNewswire. |
| `search_pages` | Generic public search/list-page adapter for company sites, standards pages, tenders, recruitment pages, project pages, and exchange pages. |
| `rss` | RSS/Atom feeds, useful for PRNewswire, company press rooms, industry bodies. |
| `cninfo_announcements` | 巨潮资讯 announcement list API; returns announcement metadata and PDF URLs. |
| `sec_edgar` | Official SEC EDGAR submissions metadata for 10-K/10-Q/8-K/6-K/20-F. |
| `json_api` | Configurable JSON API adapter for licensed data vendors, price data, import/export, tender, project filing, or internal APIs. |
| `patentsview` | PatentsView patent-search adapter. |
| `reddit` | Public Reddit JSON search for market-attention monitoring. |
| `x_recent_search` | Official X/Twitter API v2 recent search; requires bearer token. |
| `stooq_prices` | Simple public EOD price CSV from Stooq. |
| `media_crawler` | Reads existing MediaCrawler-main outputs for 雪球、财联社等. |
| `local_files` | Reads local exports from Bloomberg/Reuters terminals, licensed databases, PDFs converted to text, CSV/JSON/JSONL. |

### Example

```bash
# Preview enabled data sources
chainalpha fetch-sources --config ./configs/sources_full.yaml --query "AI 光模块 HBM 液冷" --limit 20

# Run weak-signal scan from configured data sources
chainalpha scan-sources --config ./configs/sources_full.yaml --query "AI产业链 光模块 HBM CoWoS 液冷" --out ./outputs/reports
```

### Compliance note

Some sources such as Bloomberg, Reuters, X, Weibo, and paid transcript databases require a license, login, API token, or strict terms-of-use compliance. ChainAlpha does not bypass paywalls, login walls, robots restrictions, or rate limits. For licensed sources, export data to local JSON/CSV/Markdown and ingest with `local_files`, or configure `json_api` with your official API endpoint.
