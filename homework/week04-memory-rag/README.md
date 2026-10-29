# 作业四：pi 项目长期记忆与混合检索

本项目为课程 `4-4/corpus` 的 Python 会话代码和登录资料制作独立作业副本。用户提供的 `4-3/login_demo` 是 TypeScript 登录示例，没有 Python 会话测试或过期规则；因此本练习使用课程 Python 会话模块，并补充真实的会话时间测试。复制自课程的资料见 `fixture/login_demo/materials`，测试说明按本练习的实际范围整理。原课程目录未修改。

## 安装与配置

要求 Python 3.12、Node.js 22.19 或更新版本、npm 和 uv。安装依赖：

```bash
npm ci
uv sync --locked --python 3.12
```

如果尚未安装 uv，本仓库第三周项目已有 uv 时，可用 `../week03-guarded-harness/.venv/bin/uv` 代替上面的 `uv`。截图使用锁定的 Playwright 和 Chromium。在 macOS 上默认使用本机 Google Chrome；其他环境可以执行 `npx playwright install chromium`，或通过 `HOMEWORK_PLAYWRIGHT_MODULE` 和 `HOMEWORK_CHROME` 指向现有安装。

默认读取第三周 `week03-guarded-harness/.env` 中的 `HARNESS_API_KEY`、`HARNESS_BASE_URL`、`HARNESS_MODEL`。也可以复制 `.env.example` 为本目录 `.env` 配置；已有环境变量优先。密钥不进入报告或提交文件。首次运行需下载 BGE 模型，真实 DeepSeek 调用会产生正常 API 用量。

## 一键运行

```bash
uv run --locked python demo.py --all
```

运行从 `fixture/login_demo` 创建新的独立工作副本，在 `reports/run-...` 保存状态、迁移后的项目、SQLite、三次 pi 会话的 JSONL、RAG 索引和完整结果，以及 `screenshots/` 中六张 PNG 与对应 HTML。`status.json` 只有在所有阶段成功后才标记 `complete: true`；失败时会保留现场及错误日志。

三次 pi 进程使用独立 session ID，第二次读取已持久化的记忆，第三次更新同一记录。SQLite 数据库以真实项目路径区分项目。扩展注册 `memory_write`、`memory_search`、`memory_read`、`memory_update`、`memory_delete`；来源必须是项目内真实文件，写入时记录 SHA-256。每条记录包括项目、ID、key、内容、来源、版本与时间。演示迁移后再次独立执行 pytest 验证；pi 会话中也会运行测试。

RAG 为两路索引使用同一节点 ID。本地 BGE 模型是真实 FastEmbed ONNX 模型，查询侧显式加 BGE 指令；BM25 使用 jieba 分词，持久化并恢复参数，展示原文。RRF 取两路各前 10，结果展示各路前 5。更新仅修改本次运行的资料副本，重建并切换两路索引，不改变 Python 业务代码；这是一项资料更新实验，不能据此声称业务代码的会话有效期也已改变。新进程加载版本化索引检查节点集合，模型回答引用对应的原文。DeepSeek 模型费用没有由本项目计算。

## 本地测试

```bash
uv run --locked python -m pytest -q
node --test tests/test_memory.mjs
```

Python 自动化测试使用 LlamaIndex 的 MockEmbedding 验证索引保存、中文检索、更新与 RRF；真实 BGE 及真实模型验收以一键演示结果为准。Node 测试覆盖记忆持久化、隔离、更新和删除，并在新进程验证读取。截图和报告中的数据来自对应真实执行结果，不由离线测试替代。

本次已完成的真实验收记录与六张截图见 `reports/run-20261029-100458-6deaaa/`。如仅需从现有记录重新排版截图，可运行 `uv run --locked python rerender.py reports/run-20261029-100458-6deaaa`，不会再次调用模型。
