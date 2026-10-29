# login_demo 会话测试说明

课程资料适配：此项目只包含课程 Python 会话时间判断和续期模块。

## 测试入口

- 测试文件：`tests/auth/test_session.py`
- 运行命令：`python -m pytest tests/auth/test_session.py -q`

## 覆盖范围

- 到期前会话有效，恰好到期和到期后会话无效。
- 续期会从当前时间向后延长 20 分钟。
