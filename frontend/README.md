# ChainAlpha Frontend Demo

这是 ChainAlpha 研投报告项目的前端 V0 Demo。当前版本不依赖 Node 包，也不需要后端服务，直接打开 `index.html` 即可预览工作台形态。

## 运行方式

推荐用 FastAPI 同时启动后端和前端：

```powershell
$env:PYTHONPATH="src"
python -m uvicorn chainalpha.api.app:app --reload --host 127.0.0.1 --port 8000
```

然后访问：

```text
http://127.0.0.1:8000/app/
```

这样页面会直接连接 `/api/runs`、`/api/runs/{run_id}/report` 等真实接口。

也可以只看静态 Demo：

直接打开：

```text
frontend/index.html
```

或者在项目根目录启动一个静态服务：

```powershell
python -m http.server 5173 -d frontend
```

然后访问：

```text
http://127.0.0.1:5173
```

## 当前功能

- 创建研投任务表单 Demo
- 报告类型、研究深度、LLM、产业链图谱、质量检查等配置项
- 任务阶段进度展示
- 12 节专业研报目录与正文预览
- 候选公司、核心证据、数据缺口、产物列表
- 历史任务列表

## 后续接 FastAPI

V1 后端建议提供：

```text
POST /api/runs
GET /api/runs
GET /api/runs/{run_id}
GET /api/runs/{run_id}/report
GET /api/runs/{run_id}/artifacts
```

前端只需要把 `src/mockData.js` 替换为 API 请求适配层即可。
