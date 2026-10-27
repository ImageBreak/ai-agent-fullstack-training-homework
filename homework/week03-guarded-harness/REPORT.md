# 作业三提交报告

## 实现与验证结果

在独立目录完成 Guarded Coding Harness，使用 smolagents **1.26.0 ToolCallingAgent** 的原有模型决策与执行循环，通过薄适配接入统一 Gate。包含题目指定的六个实现模块和六个测试文件，另外拆出类型、工具、模型适配与完成检查以便审阅。

已完成全部 **19 项验收：15 项 P0、4 项 P1**。机器可读结果为 `reports/verification.json`，其中 `acceptance_complete: true`。

实际环境：macOS，Python 3.12.14，pytest 9.1.1；依赖由 `uv.lock` 锁定并经 `uv sync --locked` 检查。

最终执行命令：

```text
.venv/bin/python verify.py
```

其中实际调用 `.venv/bin/python -m pytest -q --junitxml=reports/junit.xml`，结果：

```text
............................................                             [100%]
44 passed in 3.65s
```

`ruff check .` 通过。完整 pytest 输出在 `reports/pytest-result.txt`，每个验收编号对应的通过测试在 `reports/verification.json`。

## 真实模型验收

服务：DeepSeek；endpoint：`https://api.deepseek.com`；模型：**deepseek-flash**。按服务工具选择协议显式使用 non-thinking 模式；原始模型 ID 未替换，零 SDK 自动重试。配置选择记录在各运行状态的 `model_state`。

| 任务 | 状态 | 累计模型步骤 | 累计工具调用 | API 返回累计 Token | 结果 |
|---|---|---:|---:|---:|---|
| 代码理解 | COMPLETED | 8 | 7 | 25,786 | 产出带源码引用的报告；单元测试 4 passed；源码 Diff 为 0 字节 |
| 测试修复 | COMPLETED | 11 | 10 | 38,387 | 定位 upper-bound off-by-one；测试从 1 failed / 3 passed 变为 4 passed；仅源码变更 |

演示初次收到模型的多工具调用时，Gate 拒绝整批，模型随后改为单次调用。写操作都在审批处暂停，由外部操作员 `codex-operator` 核对具体参数后，通过 CLI 签发一次性审批，再从新进程恢复。该身份标签如实记录为 Codex 操作员，不声称为真人交互记录。离线演示另标记为 `scripted-test-reviewer`，不与真实模型记录混淆。

真实模型的材料：

- `reports/live-understanding/understanding.md`：代码理解报告；
- `reports/live-repair/repair.md`：修复原因、修复前后实际测试输出；
- 两目录的 `changes.diff`：真实源码差异；
- `execution.jsonl`、`state.json`：工具请求、实际结果和完整模型历史；
- `audit.jsonl`：哈希链校验通过的追加式审计；
- `checkpoint.json`、`recovery.json`：安全断点和新进程恢复记录；
- `run.json`：运行状态、模型类型、预算和最终回答。

## 关键边界的实际验证

- G1/G2：用 handler 计数证明参数拒绝不会进入执行函数，连续第三次相同动作在执行前被拦截；重复计数跨恢复保留。
- G3/G4：未批准写入保持文件不变；批准绑定具体调用、参数和工作区版本，执行前持久化消费；再次提交同一调用被拒绝。
- S1/S2/S3：路径逃逸、软链接和敏感目录、配置外命令均不执行并记录审计。
- S4：受控命令实际创建子进程；超时和取消后检查父子进程均不再运行。额外验证 OS 沙箱阻止外部文件读取、源码写入和网络访问。
- C2：子进程在文件写入后、结果提交前使用 `os._exit(86)` 模拟真实崩溃；新进程恢复为 `UNKNOWN_RESULT`，保留已消费审批并拒绝自动重放。
- C3/C4：通过独立 CLI 进程恢复原任务，保留累计预算；外部改变源码或产物会使旧证据和旧审批失效。
- A1/A2：拒绝审计含 rule/decision/actor/toolCallId；写调用能按 ID 关联 Gate、Sandbox、State、Approval 和执行结果。
- H1/H2/H3：无交付物的最终回答被拒绝；源码改变后旧测试证据失效；步数、工具或 Token 预算耗尽均以明确停止原因退出。
- 额外验证：审计篡改检测、Checkpoint 损坏检测、旧状态写入拒绝、环境变量与输出边界、父目录 pytest 配置隔离、模型异常日志凭证脱敏。

## 可复现与范围

从锁定环境运行 `verify.py` 可重现全部 44 项离线测试与两类 Fake Model 跨进程演示；真实模型结果需要使用本地 `.env` 配置，按 README 的 start / approve / resume / export 流程另行复现。

测试结果保存源码与锁文件指纹；使用 `verify.py --skip-tests` 时若源码已变，会拒绝复用过期证据。密钥没有写入提交材料，`.env` 已被 Git 忽略且权限为 0600。

当前执行隔离针对本机 macOS Seatbelt；不支持的系统会 fail closed。面向本地单操作员、受控教学仓库；支持不超过 1000 个、合计 4MB 的 UTF-8 文本文件。Checkpoint 和执行账本保存在模型可访问仓库之外。`UNKNOWN_RESULT` 需要人工核对，不自动猜测或重试。

完整使用说明和逐项验收映射见 `README.md`。
