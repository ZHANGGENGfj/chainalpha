# ChainAlpha 网页端运行说明

ChainAlpha 是一个 AI 投研报告系统。当前推荐通过网页端使用：在浏览器里输入研究问题，后端自动完成信息读取、检索增强、报告生成，并把结果保存到本地运行目录。

## 1. 安装依赖

进入项目目录：

```powershell
cd D:\chainalpha\chainalpha_mvp_generic_crawlers
```

安装项目：

```powershell
pip install -e .
```

如果要使用 LangGraph 工具调用流程，建议安装：

```powershell
pip install -e ".[agents]"
```

如果要接入部分 A 股数据源，可选安装：

```powershell
pip install -e ".[china_data]"
```

## 2. 配置大模型

在项目根目录创建或修改 `.env`，例如使用 DeepSeek：

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

### 邀请码注册

网页端默认启用邀请码注册，不需要短信验证码或任何第三方登录服务。在 `.env` 增加并自行替换以下值：

```env
AUTH_ENABLED=true
AUTH_SECRET=请替换为一段足够长的随机字符串
AUTH_BOOTSTRAP_INVITE=请设置你的首个邀请码
AUTH_BOOTSTRAP_INVITE_MAX_USES=10
```

首次使用 `AUTH_BOOTSTRAP_INVITE` 注册的账户会成为管理员；管理员可以调用 `POST /api/auth/invites` 生成后续邀请码。用户、邀请码和密码哈希保存在 `data/auth/chainalpha_auth.sqlite3`，该文件不应提交到 Git。

## 3. 启动网页端

在 PowerShell 里运行：

```powershell
cd D:\chainalpha\chainalpha_mvp_generic_crawlers
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
$env:PYTHONPATH="src"
python -m uvicorn chainalpha.api.app:app --reload --host 127.0.0.1 --port 8000
```

启动成功后打开：

```text
http://127.0.0.1:8000/app/
```

实时事件首页默认读取真实 API，不会在接口失败或没有当日数据时自动伪装成 Mock 数据：

```text
http://127.0.0.1:8000/app/
```

事件详情路由：

```text
http://127.0.0.1:8000/app/events/<event_id>
```

原有研投助手保留在：

```text
http://127.0.0.1:8000/app/research
```

需要检查第一阶段演示数据时，必须显式开启：

```text
http://127.0.0.1:8000/app/?eventData=mock
```

健康检查地址：

```text
http://127.0.0.1:8000/health
```

### 事件流配置

事件 API 默认读取现有 MediaCrawler 本地数据：

```text
third_party/MediaCrawler-main/data/finance_news
third_party/MediaCrawler-main/data/cninfo_reports
third_party/MediaCrawler-main/data/broker_reports
```

可在 `.env` 或系统环境变量中覆盖：

```env
# MediaCrawler 程序目录与事件读取目录
MEDIA_CRAWLER_ROOT=third_party/MediaCrawler-main
EVENT_SOURCE_ROOT=third_party/MediaCrawler-main/data

# 启动后立即采集，并在每轮完成 120 秒后再次采集
EVENT_AUTO_COLLECT=true
EVENT_COLLECT_INTERVAL_SECONDS=120
EVENT_COLLECT_KEYWORDS=
EVENT_COLLECT_TIMEOUT_SECONDS=600

# 归一化、去重和聚类后的事件快照
EVENT_STORE_PATH=data/event_store/events.json

# 标题相似度聚类阈值与聚类时间窗口
EVENT_SIMILARITY_THRESHOLD=0.84
EVENT_CLUSTER_WINDOW_HOURS=72

# 单进程最多同时维持的 SSE 连接数
EVENT_SSE_MAX_CONNECTIONS=50
```

### 五好精选

页面地址：`http://127.0.0.1:8000/app/five-good`

接口：

- `GET /api/five-good/overview`
- `GET /api/five-good/stocks`
- `GET /api/five-good/stocks/{stock_code}`
- `GET /api/five-good/stocks/{stock_code}/history`

真实筛选结果写入 `data/five_good/store.json`。文件不存在时可通过
`FIVE_GOOD_DEMO_DATA=true` 启用明确标识的演示数据；生产环境应关闭该开关。

全市场生产名单由横截面计算生成，不由前端或固定公司名单决定。把 `TUSHARE_TOKEN`
写入 `.env` 后执行：

```powershell
$env:PYTHONPATH="src"
python .\run_five_good_screening.py --refresh-data
```

首次运行会获取全部上市公司、财务指标、估值、约 70 个交易日行情和资金流，并读取本地
事件库。标准化特征缓存到 `data/five_good/features.json`，筛选结果写入
`data/five_good/store.json`；仅调整规则并重新计算时可去掉 `--refresh-data`。

五个池分别计算：好公司看质量和现金流，好价格看行业内估值与增长匹配，好时机看趋势、
相对强弱、量能与资金，好故事只接受映射到公司的有效事件，好股票综合基本面质量、行情、
行业景气、流动性和事件。数据完整度不足的公司不会被强行填分。

当前项目没有数据库迁移框架，因此第二阶段没有虚构数据库迁移；事件仓库使用原子替换的 JSON
快照。公开部署时还应在 Nginx 或 API 网关补充跨进程限流。

### 事件 API

```text
GET /api/events/dashboard
GET /api/events
GET /api/events/{event_id}
GET /api/events/{event_id}/timeline
GET /api/sectors/hot
GET /api/events/distribution
GET /api/events/tracking
GET /api/events/stream
GET /api/events/refresh/status
POST /api/events/refresh
```

`/api/events` 使用不透明游标分页，单页最多 50 条。`/api/events/stream` 使用 SSE 发送变化提示；
浏览器断线后采用指数退避重连，连续失败后降级为 30 秒轮询。

新闻采集在后台独立运行，不阻塞首页请求。MediaCrawler 首先执行 URL、30 天精确标题和
7 天模糊标题去重，并将当天数据合并到同一个文件；事件服务随后再次执行 URL、内容 Hash、
实体、标题相似度和时间窗口聚类。若一轮采集超过 120 秒，系统不会启动重叠任务。

## 4. 网页端运行逻辑

网页端提交问题后，大致流程是：

1. 前端调用后端接口 `POST /api/runs`。
2. 后端启动 `run_topic_research.py`。
3. 系统根据问题做查询改写和研究类型判断。
4. 按需要刷新 MediaCrawler，并读取本地新闻/公告/研报数据。
5. 使用 LangGraph 工具检索 Agent 补充本地新闻、网页证据和缺失信息。
6. 先写入统一证据层 `evidence_store.json`，再基于证据层生成研究底稿。
7. 调用大模型生成最终投研报告。
8. 报告质检 Agent 默认只审稿，不再重复联网检索。
9. 输出结果保存到 `data/topic_runs/<运行时间>/`。

常见输出文件：

```text
final_report.md
final_report.html
topic_research.json
query_expansion.json
evidence_store.json
tool_research_agent.json
report_quality_audit.json
```

如果是产业链研究，还可能生成：

```text
chain_visual.svg
chain_visual.json
```

## 5. 常见问题

如果遇到：

```text
PermissionError: [Errno 13] Permission denied: 'C:\ProgramData\DLPsslLog\log.txt'
```

先在当前 PowerShell 里执行：

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
```

如果提示没有配置大模型 key，检查 `.env` 是否在项目根目录，并确认 `DEEPSEEK_API_KEY` 已填写。

如果提示 `langgraph` 不存在，执行：

```powershell
pip install -e ".[agents]"
```

如果网页报告里新闻较少，检查本地新闻目录：

```text
third_party/MediaCrawler-main/data
```

## 6. 金融能力评测

项目提供两个相互独立的评测适配器：

- `FinResearchBench`：评估长篇金融研究报告，输出六项 LLM 质评指标和四项逻辑树规则指标。
- `FinGAIA`：评估金融任务答案，输出总体准确率以及按场景、难度等级拆分的准确率。

FinResearchBench 公开论文描述了 70 个研究任务和十维评测框架，但没有发布完整官方评测程序；FinGAIA 公开仓库说明完整集有 407 题，但公开可直接取得的数据可能只是开发子集。因此，本项目输出会明确标记为 `compatible_reproduction` 或 `deterministic_answer_match`，不得冒充官方排行榜成绩。

评估一份现有报告：

```powershell
$env:PYTHONPATH="src"
python .\run_fin_benchmark.py finresearchbench `
  --tasks .\configs\benchmarks\finresearchbench.example.jsonl `
  --single-report .\data\topic_runs\<run_id>\final_report.md
```

不调用 LLM，只检查四项规则指标：

```powershell
python .\run_fin_benchmark.py finresearchbench `
  --tasks .\configs\benchmarks\finresearchbench.example.jsonl `
  --single-report .\data\topic_runs\<run_id>\final_report.md `
  --rule-only
```

评估 FinGAIA 格式的答案文件：

```powershell
python .\run_fin_benchmark.py fingaia `
  --tasks .\configs\benchmarks\fingaia.example.jsonl `
  --predictions .\configs\benchmarks\fingaia_predictions.example.jsonl
```

任务文件兼容 JSON/JSONL，常见字段名如 `id/task_id`、`question/query`、`answer/ground_truth`、`scenario/category`、`level/difficulty` 和 `attachments/files` 会自动映射。预测既可以是 JSON/JSONL，也可以是以题号命名的 Markdown 目录。

每次评测生成：

```text
data/benchmark_runs/<benchmark>_<timestamp>/summary.json
data/benchmark_runs/<benchmark>_<timestamp>/summary.md
data/benchmark_runs/<benchmark>_<timestamp>/details.jsonl
```

资料入口：

- FinResearchBench 论文：<https://arxiv.org/abs/2507.16248>
- FinGAIA 官方仓库：<https://github.com/SUFE-AIFLM-Lab/FinGAIA>

## 7. 运行时金融 Skills 与 Agent 状态

公司研究流程现在使用 `skills/financial-evidence-research/manifest.yaml` 声明证据能力、查询模板、可用工具和来源优先级。新闻、公告、财务、营运资本、业务结构与行情检索不再由 `tool_research_agent.py` 中的固定关键词阶梯控制。

运行时结构：

```text
研究问题
  -> 读取 Runtime Skills
  -> 判断证据能力缺口
  -> 结合能力级历史效果选择 local_search/targeted_news_crawl/web_search
  -> 执行 Connector、过滤与去重
  -> 保存工具效果和证据记忆
  -> 证据仍不足则继续循环
  -> 生成报告并质检
```

持久数据默认写入：

```text
data/agent_states/<run_id>.json
data/agent/tool_performance.json
data/research_memory/evidence.sqlite3
```

The web API enables demand-driven collection by default. The research Agent
searches local and remembered evidence first, and only calls MediaCrawler when
the active Skill still has a time-sensitive evidence gap. Use
`--skip-refresh-news` to disable that on-demand crawler call for an offline run.

- Agent状态快照支持同一任务中断后恢复。
- 工具效果按 `Skill + capability + tool` 记录成功率、证据增量、耗时和最近错误，用于后续路由。
- 研究证据记忆只保存来源材料及其时间，不把LLM结论当成事实。
- Mem0继续只保存用户报告偏好，与金融事实证据隔离。

新增采集能力时，先实现确定性的 Connector/Tool，再在 Skill manifest 中声明能力、工具、证据覆盖词和查询模板。不要让模型直接修改爬虫选择器或绕过来源验证。
