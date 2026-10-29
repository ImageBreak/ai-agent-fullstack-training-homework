"""Run the two homework demonstrations, preserving actual events and screenshots."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
import rag
import report

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / 'fixture/login_demo'


def environment():
    config = dotenv_values(ROOT.parent / 'week03-guarded-harness/.env')
    config.update({k:v for k,v in dotenv_values(ROOT / '.env').items() if v})
    config.update({k:os.environ[k] for k in ('HARNESS_API_KEY','HARNESS_BASE_URL','HARNESS_MODEL') if os.environ.get(k)})
    missing = [k for k in ('HARNESS_API_KEY','HARNESS_BASE_URL','HARNESS_MODEL') if not config.get(k)]
    if missing:
        raise ValueError('缺少配置：' + ', '.join(missing))
    env = os.environ.copy()
    env.update({k:str(config[k]) for k in ('HARNESS_API_KEY','HARNESS_BASE_URL','HARNESS_MODEL')})
    env['PATH'] = str(ROOT / '.venv/bin') + os.pathsep + env.get('PATH', '')
    env['HOMEWORK_MODEL_CACHE'] = str(ROOT / '.runtime/models')
    env['HF_HOME'] = str(ROOT / '.runtime/huggingface')
    env['HF_HUB_DISABLE_XET'] = '1'
    env['PI_CODING_AGENT_DIR'] = str(ROOT / '.runtime/pi-agent')
    env['NODE_USE_SYSTEM_CA'] = '1'
    return env


def save(path, obj):
    rag.save_json(path, obj)


def read_record(db, root):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute('SELECT * FROM memory WHERE project = ?', (str(root.resolve()),)).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f'应有一条当前项目记忆，实际 {len(rows)} 条')
    record = dict(rows[0]); record['sources'] = json.loads(record['sources']); record['database_path'] = str(db)
    return record


def pi_run(phase, prompt, workspace, run, env, expected):
    logdir = run / 'memory'; sessions = logdir / 'sessions'; sessions.mkdir(parents=True, exist_ok=True)
    cmd = [str(ROOT/'node_modules/.bin/pi'), '--mode','json','--offline','--no-extensions','--extension',str(ROOT/'extensions/project-memory.ts'), '--no-skills','--no-prompt-templates','--no-themes','--no-context-files', '--provider','homework-deepseek','--model',env['HARNESS_MODEL'],'--thinking','off','--session-dir',str(sessions),prompt]
    proc = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=360)
    (logdir/f'{phase}.events.jsonl').write_text(proc.stdout, encoding='utf-8')
    (logdir/f'{phase}.stderr.txt').write_text(proc.stderr, encoding='utf-8')
    if proc.returncode:
        raise RuntimeError(f'pi {phase} 退出码 {proc.returncode}；查看 {logdir / (phase + ".stderr.txt")}')
    try:
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as e:
        raise RuntimeError(f'pi JSON 格式错误：{e}') from e
    if not events or events[0].get('type') != 'session':
        raise RuntimeError('缺少 pi 会话头')
    session_files = list(sessions.glob(f"*_{events[0]['id']}.jsonl"))
    if len(session_files) != 1:
        raise RuntimeError('找不到本次会话文件')
    session_entries = [json.loads(line) for line in session_files[0].read_text(encoding='utf-8').splitlines() if line.strip()]
    user_entries = [entry for entry in session_entries if entry.get('type') == 'message' and entry.get('message', {}).get('role') == 'user']
    if session_entries[0].get('parentSession') or len(user_entries) != 1:
        raise RuntimeError('会话包含历史消息或存在父会话')
    if not any(block.get('text') == prompt for block in user_entries[0]['message'].get('content', []) if isinstance(block, dict)):
        raise RuntimeError('会话输入与本阶段提示词不一致')
    if not any(e.get('type') == 'agent_settled' for e in events):
        raise RuntimeError('pi 未完成 agent_settled')
    messages = [e.get('message', {}) for e in events if e.get('type') == 'message_end' and e.get('message', {}).get('role') == 'assistant']
    if not messages or messages[-1].get('stopReason') != 'stop' or messages[-1].get('errorMessage'):
        raise RuntimeError(f'pi 最终消息失败：{messages[-1] if messages else "无消息"}')
    ends = [e for e in events if e.get('type') == 'tool_execution_end']
    for name in expected:
        matches = [e for e in ends if e.get('toolName') == name]
        if not matches or all(e.get('isError') for e in matches):
            raise RuntimeError(f'pi 没有成功调用 {name}')
    text = '\n'.join(x.get('text','') for x in messages[-1].get('content',[]) if x.get('type') == 'text')
    result = {'session':events[0], 'answer':text, 'tools':[e.get('toolName') for e in ends], 'event_log':str(logdir/f'{phase}.events.jsonl'), 'session_file':str(session_files[0])}
    save(logdir/f'{phase}.summary.json',result)
    return result


def screenshot(run, name, title, subtitle, sections):
    out = run / 'screenshots'; out.mkdir(parents=True, exist_ok=True)
    report.render(out / f'{name}.html', title, subtitle, sections, out / f'{name}.png')


def worker(action, corpus, storage, output, run, env):
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([sys.executable,str(ROOT/'rag_worker.py'),action,'--corpus',str(corpus),'--storage',str(storage),'--output',str(output)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    (run/'rag'/f'{output.stem}.stdout.txt').write_text(proc.stdout, encoding='utf-8')
    (run/'rag'/f'{output.stem}.stderr.txt').write_text(proc.stderr, encoding='utf-8')
    if proc.returncode:
        raise RuntimeError(f'RAG {action} 失败，见 {run / "rag" / (output.stem + ".stderr.txt")}')
    return json.loads(output.read_text(encoding='utf-8'))


def do_all(run):
    env = environment()
    workspace = run / 'login_demo'
    shutil.copytree(FIXTURE, workspace)
    memory = run / 'memory'; memory.mkdir(parents=True)
    db = memory / 'memory.sqlite3'; env['HOMEWORK_MEMORY_DB'] = str(db)
    write_prompt = ('读取当前项目的 tests/test_session.py 和 materials/testing-guide.md。使用 memory_write 保存会话测试路径和准确运行命令，key 为 session-test，内容应包含“会话测试”和命令；sources 必须是这两个相对路径。最后回答保存的实际数据库路径和来源。')
    first = pi_run('write',write_prompt,workspace,run,env,['memory_write'])
    record1 = read_record(db,workspace)
    save(memory/'write.record.json',record1)
    screenshot(run,'q1-01','第一题 · 长期记忆写入','真实 pi 会话与 SQLite 持久化记录', [('会话与工具调用',report.pi_summary(first)),('实际保存记录',report.record_summary(record1))])
    read_prompt = ('从当前项目长期记忆中用 memory_search 查找“会话测试”，再用 memory_read 读取记录。核对记忆中当前测试文件是否存在，并读取测试说明验证运行命令。回答记忆与当前文件的对比以及来源。')
    second = pi_run('read',read_prompt,workspace,run,env,['memory_search','memory_read'])
    if second['session']['id'] == first['session']['id']:
        raise RuntimeError('读取没有使用新会话')
    record2 = read_record(db,workspace)
    if record2 != record1 or not (workspace/'tests/test_session.py').exists():
        raise RuntimeError('读取记忆或文件核对失败')
    screenshot(run,'q1-02','第一题 · 新会话查询与核对','独立会话读取同一持久化记忆，并核对项目文件', [('会话 ID 对比',report.pre({'写入':first['session']['id'],'查询':second['session']['id']})),('查询结果及回答',report.pi_summary(second)),('记忆与文件（查询时路径存在）',report.record_summary(record2))])
    move_prompt = ('将 tests/test_session.py 迁移到 tests/auth/test_session.py（保留测试内容），同步把 materials/testing-guide.md 里的测试路径和运行命令修改为新路径。运行 python -m pytest tests/auth/test_session.py -q。然后用 memory_search 和 memory_read 找到原记忆，使用 memory_update 更新原记录的路径、命令和实际测试结果，sources 为新测试文件和测试说明。回答实际结果。')
    third = pi_run('update',move_prompt,workspace,run,env,['memory_search','memory_read','memory_update'])
    if len({first['session']['id'],second['session']['id'],third['session']['id']}) != 3:
        raise RuntimeError('三个会话 ID 必须不同')
    new = workspace/'tests/auth/test_session.py'
    if not new.exists() or (workspace/'tests/test_session.py').exists():
        raise RuntimeError('测试文件迁移不完整')
    if new.read_bytes() != (FIXTURE/'tests/test_session.py').read_bytes():
        raise RuntimeError('测试迁移改变了测试内容')
    guide = (workspace/'materials/testing-guide.md').read_text(encoding='utf-8')
    if 'tests/auth/test_session.py' not in guide or 'tests/test_session.py' in guide:
        raise RuntimeError('测试说明没有同步')
    verify = subprocess.run([sys.executable,'-m','pytest','tests/auth/test_session.py','-q'],cwd=workspace,env=env,capture_output=True,text=True,timeout=60)
    (memory/'pytest.txt').write_text(verify.stdout+verify.stderr,encoding='utf-8')
    if verify.returncode:
        raise RuntimeError('迁移后的测试没有通过')
    record3 = read_record(db,workspace)
    if record3['id'] != record1['id'] or record3['revision'] <= record1['revision'] or 'tests/auth/test_session.py' not in record3['content']:
        raise RuntimeError('长期记忆没有更新原记录')
    save(memory/'update.record.json',record3)
    screenshot(run,'q1-03','第一题 · 测试迁移与记忆更新','同一记录 ID，新的来源、版本与实际测试结果', [('pi 更新回答',report.pi_summary(third)),('实际测试结果',report.pre(verify.stdout)),('更新后的 SQLite 记录',report.record_summary(record3))])
    corpus = run/'rag'/'corpus'; shutil.copytree(workspace/'materials',corpus)
    storage = run/'rag'/'index'; resultdir=run/'rag'; resultdir.mkdir(exist_ok=True)
    initial = worker('build',corpus,storage,resultdir/'initial-build.json',run,env)
    if initial['dimension'] != 512: raise RuntimeError('真实 BGE 维度不是 512')
    screenshot(run,'q2-01','第二题 · 构建向量与关键词索引','LlamaIndex 同一批节点 + 本地真实 BGE Embedding', [('建库结果与分块样例',report.pre(initial))])
    q1 = worker('query',corpus,storage,resultdir/'initial-query.json',run,env)
    if not re_answer(q1,30): raise RuntimeError('初始回答未确认 30 分钟及其来源')
    screenshot(run,'q2-02','第二题 · 三路检索与引用回答','同一问题、各路前 5 条；真实模型使用混合检索证据', [('检索结果对比',report.comparison(q1)),('真实模型回答与引用',report.generation_summary(q1['generation']))])
    changed = rag.update_corpus(corpus)
    updated = worker('build',corpus,storage,resultdir/'updated-build.json',run,env)
    q2 = worker('query',corpus,storage,resultdir/'updated-query.json',run,env)
    removed = sorted(set(q1['node_ids'])-set(q2['node_ids']))
    if not removed or set(removed).intersection(q2['node_ids']) or not re_answer(q2,20):
        raise RuntimeError('更新结果或引用未通过验证')
    summary = {'changed_files':changed,'old_version':initial['version'],'new_version':updated['version'],'removed_node_count':len(removed),'removed_node_ids':removed,'current_node_count':len(q2['node_ids'])}
    save(resultdir/'update-verification.json',summary)
    screenshot(run,'q2-03','第二题 · 资料与索引更新','旧变更节点退出当前索引，新进程重新加载并引用新资料', [('更新验证',report.pre(summary)),('真实模型回答与引用',report.generation_summary(q2['generation']))])
    return {'complete':True,'run_directory':str(run),'screenshots':[str(run/'screenshots'/f'q{q}-{i:02}.png') for q in (1,2) for i in (1,2,3)]}


def re_answer(result, minutes):
    import re
    generation = result['generation']
    answer = generation['answer']
    refs = set(re.findall(r'\[(E\d+)\]',answer))
    cited = [e for e in generation['evidence'] if e['id'] in refs]
    return generation['citation_ids_valid'] and str(minutes) in answer and any(re.search(rf'{minutes}\s*分钟',e['text']) for e in cited)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--all',action='store_true',required=True); p.add_argument('--output',type=Path)
    args = p.parse_args()
    run = (args.output or ROOT/'reports'/f'run-{time.strftime("%Y%m%d-%H%M%S")}-{uuid4().hex[:6]}').resolve()
    run.mkdir(parents=True,exist_ok=False)
    try:
        result=do_all(run); save(run/'status.json',result); print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as error:
        save(run/'status.json',{'complete':False,'error':f'{type(error).__name__}: {error}'})
        print(f'演示未完成：{type(error).__name__}: {error}\n记录目录：{run}',file=sys.stderr)
        raise

if __name__ == '__main__': main()
