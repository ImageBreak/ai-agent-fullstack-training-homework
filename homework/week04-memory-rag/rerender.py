"""Regenerate report screenshots from an existing successful run without model calls."""
import argparse
import json
from pathlib import Path
import demo
import report

p = argparse.ArgumentParser()
p.add_argument('run', type=Path)
a = p.parse_args()
run = a.run.resolve()
if not json.loads((run/'status.json').read_text(encoding='utf-8'))['complete']:
    raise ValueError('只能重排完整成功的运行')

def get(path):
    return json.loads((run/path).read_text(encoding='utf-8'))

first, second, third = (get(f'memory/{phase}.summary.json') for phase in ('write','read','update'))
record1, record3 = (get(f'memory/{phase}.record.json') for phase in ('write','update'))
pytest_text = (run/'memory/pytest.txt').read_text(encoding='utf-8')
initial, q1, q2, summary = (get(f'rag/{name}.json') for name in ('initial-build','initial-query','updated-query','update-verification'))
demo.screenshot(run,'q1-01','第一题 · 长期记忆写入','真实 pi 会话与 SQLite 持久化记录', [('会话与工具调用',report.pi_summary(first)),('实际保存记录',report.record_summary(record1))])
demo.screenshot(run,'q1-02','第一题 · 新会话查询与核对','独立会话读取同一持久化记忆，并核对项目文件', [('会话 ID 对比',report.pre({'写入':first['session']['id'],'查询':second['session']['id']})),('查询结果及回答',report.pi_summary(second)),('记忆与文件（查询时路径存在）',report.record_summary(record1))])
demo.screenshot(run,'q1-03','第一题 · 测试迁移与记忆更新','同一记录 ID，新的来源、版本与实际测试结果', [('pi 更新回答',report.pi_summary(third)),('实际测试结果',report.pre(pytest_text)),('更新后的 SQLite 记录',report.record_summary(record3))])
demo.screenshot(run,'q2-01','第二题 · 构建向量与关键词索引','LlamaIndex 同一批节点 + 本地真实 BGE Embedding', [('建库结果与分块样例',report.pre(initial))])
demo.screenshot(run,'q2-02','第二题 · 三路检索与引用回答','同一问题、各路前 5 条；真实模型使用混合检索证据', [('检索结果对比',report.comparison(q1)),('真实模型回答与引用',report.generation_summary(q1['generation']))])
demo.screenshot(run,'q2-03','第二题 · 资料与索引更新','旧变更节点退出当前索引，新进程重新加载并引用新资料', [('更新验证',report.pre(summary)),('真实模型回答与引用',report.generation_summary(q2['generation']))])
print(f'已从现有真实记录重排六张截图：{run / "screenshots"}')
