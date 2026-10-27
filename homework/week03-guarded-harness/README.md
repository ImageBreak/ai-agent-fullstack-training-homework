# 作业三：可恢复的 Guarded Coding Harness

在独立的 `week03-guarded-harness` 目录完成。使用真正的 **smolagents 1.26.0 `ToolCallingAgent`**，没有修改依赖源码，没有另写模型决策循环。支持代码理解和测试修复两类任务。

## 环境与运行

已验证环境：macOS、Python 3.12；依赖由 `pyproject.toml` 和 `uv.lock` 固定。测试命令在 macOS 原生 Seatbelt 沙箱内执行。其他系统或原生沙箱不可用时拒绝执行，不退回无隔离模式。

如果已有 uv：

```bash
cd homework/week03-guarded-harness
uv sync --locked --python 3.12
.venv/bin/python -m pytest -q
.venv/bin/python verify.py
```

如果本机没有 uv，可在已有的 Python 3.12 环境中引导安装：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install uv
.venv/bin/uv sync --locked
.venv/bin/python verify.py
```

`verify.py` 会运行全部离线测试，生成两类任务的完整演示，且在**不同 OS 进程**中执行审批和恢复。无需 API Key。演示的 `scripted-test-reviewer` 是校验固定动作的测试替身，记录中明确标注，不冒充真人审批。

运行产物位于 `reports/`：

- `pytest-result.txt`、`junit.xml`：实际 pytest 输出和机器可读结果；
- `verification.json`：G/S/C/A/H/T 全部 19 项的状态和对应测试；
- `fake-understanding/`、`fake-repair/`：完整运行、审计、Checkpoint、恢复记录、任务报告和 Diff；
- `live-understanding/`、`live-repair/`：真实模型验收导出（真实运行完成后才生成）。

真实模型尚未验证时，T2 为 `NOT_RUN`，`acceptance_complete` 为 `false`。离线通过不会伪装成全部验收完成。

## 模块和执行路径

```text
用户 / 本地操作员
  └─ Harness：任务生命周期、暂停/恢复、最终完成判定
       └─ GuardedAgent（ToolCallingAgent 的薄适配层）
            └─ smolagents 原有 run / step / process_tool_calls 循环
                 └─ execute_tool_call
                      └─ Gate：参数 → 重复/重放 → 权限/风险 → Sandbox 预检 → 审批
                           └─ 持久化 STARTED 意图和一次性审批消费
                                └─ 原有 smolagents 工具分发 → 受控 handler
                                     └─ 结果 / 当前版本证据 → State / Audit / Checkpoint
```

`process_tool_calls` 的薄适配仅限制每轮一个调用、绑定原始调用 ID，然后调用父类实现。拒绝整批多调用，避免审批暂停后同批其他动作继续执行。所有工具（含未知工具、内置 `final_answer`）都经过 Gate；工具只接收经验证的 JSON 字面量，关闭框架的状态变量替换，避免审批参数与实际执行参数不一致。

| 文件 | 职责 |
|---|---|
| `gate.py` | 参数严格校验、权限和读写风险规则、连续重复拦截、ID 重放保护、一次性审批、统一执行意图与证据提交 |
| `sandbox.py` | 原始路径与真实落点校验、逐层 `O_NOFOLLOW`、原子写入、固定测试命令、原生 OS 沙箱、清洁环境、输出与进程组边界 |
| `state_store.py` | SQLite FULL 同步持久化，保存 Run、计划、证据、预算、调用账本、审批、停止原因；版本检查阻止旧实例覆盖新状态 |
| `checkpoint.py` | 带格式版本和摘要的 JSON，原子替换与 fsync；仅在没有未完成写意图的安全边界保存 |
| `audit.py` | O_APPEND + fsync 追加记录，序号和哈希链校验，按 run_id / toolCallId 关联门禁、审批、执行和状态 |
| `harness.py` | 组合组件，恢复文本历史，管理生命周期并在框架返回后独立执行完成检查 |
| `completion.py` | 验证报告、真实源码引用、当前源码版本的测试证据及任务对应的 Diff 条件 |
| `model_backend.py` | 真模型适配、脚本化 Fake Model、跨恢复的步数/工具/Token 预算 |
| `tools.py` | 列文件、读文件、搜索、精确替换、写报告、运行测试和保存计划 |

`step_callbacks` 是事后回调，只用来保存历史和安全步骤；执行前门禁放在工具调用入口。`final_answer` 校验不通过时抛出可供模型纠正的工具错误，不能返回“失败”字符串并让框架误判为最终回答。

## 审批和恢复的语义

1. 未批准的 `apply_patch` 和 `write_report` 进入 `WAITING_APPROVAL`，保存完整待审批调用，handler 不执行。
2. 本地操作员审批绑定 run、调用 ID、工具、规范化完整参数、源码与产物版本、有效期。模型不能提交身份、权限或批准字段。
3. 执行前先持久化审批已消费和 `STARTED` 意图；成功结果提交后标记 `SUCCEEDED`。已执行 ID 一律拒绝再次执行。
4. 每个安全工具/Agent 步骤保存 Checkpoint，持久化计划、历史、证据、累计预算、重复计数和待审批动作。
5. 新进程恢复同时检查 Checkpoint 和更靠后的 SQLite 执行账本。发现写调用停留在 `STARTED` 时进入 `UNKNOWN_RESULT`，不得自动重放。
6. 恢复发现源码、测试或产物变化时，旧证据和未使用审批失效，进入 `NEEDS_REVIEW`。本地操作员显式 `review` 后才继续。

`UNKNOWN_RESULT` 保留现场供人工核对；本版本不提供自动“猜测成功/失败”的命令。操作员应检查文件、Diff 和账本，保留审计后决定后续处置。它是有意暂停的结果，不是恢复失败后自动重试。

状态是业务状态；Checkpoint 是安全断点；审计是历史事实。`agent.save()` 的配置/工具导出不承担本项目的崩溃恢复。

## 完成条件

- 两类任务都必须有对应报告、可核对的 `src/file.py:line` 引用和当前源码版本的通过测试记录。
- 代码理解保持源码和测试不变，Diff 为空；报告写在 `artifacts/understanding.md`。
- 测试修复必须有真实源码修改，受保护的测试文件不可修改；报告写在 `artifacts/repair.md`。
- 测试证据绑定源码/测试指纹。后续源码修改会使旧证据失效，必须重新读源码、运行当前测试。
- 产物单独建立指纹，写报告不会使同一源码版本的测试过期；外部修改产物会使恢复暂停，且报告内容仍需匹配写入证据。
- 步数、工具预算或 Token 预算耗尽以明确 StopReason 停止，不能记为成功。计数在跨进程恢复后保留。

报告的引用和文件依据由程序检查；自然语言解释的语义质量仍应由评审者阅读。模型服务未提供 Token usage 时采用保守字符估算并标记 `estimated_usage`；Token 预算在请求前与响应后检查，不能撤销已发生的服务端费用。

## 逐项验收映射

| 编号 | 优先级 | 实现与证据 |
|---|---|---|
| G1 | P0 | 缺参数、错误类型、额外身份/批准字段拒绝，handler 计数 0：`test_gate.py::test_g1_*` |
| G2 | P0 | 第三次同工具和参数执行前拦截，前两次实际执行；恢复不清空重复计数 |
| G3 | P0 | 未批准写入进入 WAITING_APPROVAL，目标文件保持原样 |
| G4 | P0 | 同一批准调用只执行一次；重放和修改参数均拒绝 |
| S1 | P0 | `artifacts/../../.env`、绝对路径等在进入 handler 前拒绝 |
| S2 | P1 | 软链逃逸和敏感路径拒绝，并产生拒绝审计 |
| S3 | P0 | `run_test` 仅接受配置名，配置外命令和注入 command 字段不执行 |
| S4 | P1 | 超时/取消真正杀死进程组；测试检查父进程及子进程均已终止 |
| C1 | P0 | 安全断点包含 Run、步骤、计划、证据、预算、审批和待处理动作 |
| C2 | P0 | 子进程在写入后 `os._exit(86)`，新进程恢复为 UNKNOWN_RESULT，不重复写入 |
| C3 | P0 | CLI 跨进程恢复，找到原进度、写入只发生一次，预算不清零 |
| C4 | P0 | 外部修改源码或产物后，旧证据/审批失效并停止核对 |
| A1 | P0 | 拒绝记录包含 rule、decision、actor、toolCallId |
| A2 | P1 | 同一调用关联 Gate、Sandbox、State、Approval 和执行结果 |
| H1 | P0 | 无交付物时 final_answer 被拒绝，不进入完成状态 |
| H2 | P0 | 测试通过后再次改源码，旧测试证据失效；重测当前源码才能完成 |
| H3 | P0 | 步数、Token、工具调用预算耗尽均有明确 StopReason |
| T1 | P0 | 六个指定测试文件用 Fake Model 覆盖正常与失败路径，全部离线、可重复运行 |
| T2 | P1 | 真实模型分别跑通理解/修复，导出产物、Diff、测试记录与审计交叉印证 |

## 手动运行和审批

以下演示在新工作区进行，不会修改前两周作业：

```bash
.venv/bin/python cli.py start --kind repair --fixture \
  --workspace .workspaces/manual-repair --model fake
```

输出给出 `run_directory`、状态和完整 `pending`。核对具体修改后：

```bash
.venv/bin/python cli.py approve .runs/RUN_ID --call-id patch --actor local-user
.venv/bin/python cli.py resume .runs/RUN_ID
```

再次暂停写报告时，核对报告内容，再批准 `report` 并 resume。真实模型的调用 ID 由模型返回，以实际 `pending.id` 为准。

```bash
.venv/bin/python cli.py status .runs/RUN_ID
.venv/bin/python cli.py export .runs/RUN_ID --destination reports/manual-repair
```

工作区变化时先查看状态、Diff 和审批内容，确认接受当前工作区后：

```bash
.venv/bin/python cli.py review .runs/RUN_ID --actor local-user
.venv/bin/python cli.py resume .runs/RUN_ID
```

旧审批不会因此复活，后续写入必须重新请求。

## DeepSeek 真实模型验收

本地 `.env` 已创建并被 Git 忽略，填入自己的密钥即可。配置名：`HARNESS_API_KEY`、`HARNESS_BASE_URL`、`HARNESS_MODEL`。本次指定 endpoint 为 `https://api.deepseek.com`，模型为 `deepseek-flash`。程序使用配置的原始模型 ID；服务拒绝时会记录失败，不会静默切换模型。

```bash
.venv/bin/python cli.py start --kind understanding --fixture \
  --workspace .workspaces/live-understanding --model live
.venv/bin/python cli.py start --kind repair --fixture \
  --workspace .workspaces/live-repair --model live
```

每次审批都通过本地 CLI 明确执行，之后 resume。完成后分别导出为 `reports/live-understanding` 和 `reports/live-repair`，再运行 `verify.py` 更新 T2 结果。

模型请求使用显式超时与零 SDK 自动重试。真实运行可能产生模型费用。密钥只用于宿主模型客户端，不保存到状态/Checkpoint，不传入测试进程，不写审计。

DeepSeek 默认启用 thinking，而 smolagents 使用 `tool_choice=required`；按 [DeepSeek 官方说明](https://api-docs.deepseek.com/api/create-chat-completion/)，该组合会返回 400。适配器对 `api.deepseek.com` 显式发送 `thinking.type=disabled`，保留用户指定的 `deepseek-flash` 模型，并在运行配置中记录该选择。

## 执行边界

当前实现面向本地教学仓库和单用户操作员，限制为 1000 个、总计 4MB 的 UTF-8 文本文件。状态目录必须位于目标仓库之外，并使用本地锁防止同一 Run 并发执行。

原生沙箱默认拒绝权限，仅允许工作区和 Python/系统运行库读取，允许受控临时目录写入，禁止网络，执行入口限定为 Python。仓库源码对测试进程只读，修改由审批后的写工具完成。进程环境不继承宿主密钥；stdout/stderr 有字节上限。原生机制不可用时 fail closed。

审计哈希链用于检测意外编辑和顺序破坏，不声称能够抵御拥有宿主写权限的攻击者重写整条链；操作员身份是本地可信调用者提供的标签，本项目没有网络身份认证服务。
