# 作业四验收说明

本目录实现了 pi 长期记忆和 LlamaIndex 向量、BM25、RRF 混合检索。使用课程 Python `session.py` 建立独立练习副本，补充会话时间边界和续期测试；课程 TypeScript `4-3/login_demo` 没有作业所指的 Python 测试，因此没有把其测试入口当作已存在的事实。

运行 `uv run --locked python demo.py --all` 后，查阅生成的 `reports/run-.../status.json` 和 `screenshots/`。每个阶段使用真实结果生成截图：

1. `q1-01.png`：写入项目长期记忆、来源摘要和 SQLite 路径。
2. `q1-02.png`：新会话查询、读记忆和当前文件核对。
3. `q1-03.png`：测试迁移、pytest 结果和同 ID 更新后的记忆。
4. `q2-01.png`：真实 BGE 建库、节点数量和带来源样例。
5. `q2-02.png`：向量、BM25、RRF 各前五条以及真实模型回答和引用。
6. `q2-03.png`：资料改为 20 分钟、索引重建和引用新资料的真实回答。

截图对应的原始 pi 事件在 `memory/*.events.jsonl`，pi 的会话文件在 `memory/sessions/`；RAG 请求证据、回答和版本验证在 `rag/*.json`。资料更新演示只更新 RAG 资料副本；原课程资料和 Python 会话实现中的 30 分钟没有被改写。

## 本次真实验收结果

完成的记录位于 `reports/run-20261029-100458-6deaaa/`，状态为 `complete: true`。六张截图均在该目录的 `screenshots/` 中。

- pi 的写入、查询和更新分别来自三个不同的新会话；每个会话文件只有一条用户输入。原记录 ID 保持一致，版本从 1 更新为 2。
- 测试文件迁移后实际结果为 `4 passed`；独立 Node 记忆测试和三个 Python RAG 测试通过。
- 使用 `BAAI/bge-small-zh-v1.5` 生成 512 维向量，基于 22 个节点建立两路索引；向量、BM25、RRF 分别展示 5 条结果。
- 第一次真实模型回答为 30 分钟，更新后的真实模型回答为 20 分钟；两次引用编号均通过核对，更新后 6 个发生变化的旧节点退出当前索引。
