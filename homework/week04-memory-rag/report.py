"""Render saved execution results as readable screenshots, never fabricate results."""
import html
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def esc(value):
    return html.escape(str(value))


def render(path: Path, title: str, subtitle: str, sections: list[tuple[str, str]], screenshot: Path):
    cards = ''.join(f'<section><h2>{esc(name)}</h2><div>{body}</div></section>' for name, body in sections)
    text = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    *{{box-sizing:border-box}} body{{margin:0;background:#edf1f5;color:#172332;font:18px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;line-height:1.55}}
    main{{width:1380px;margin:0 auto;padding:40px}} header{{background:#12314b;color:white;padding:30px;border-radius:18px;margin-bottom:24px}}
    h1{{font-size:32px;margin:0 0 6px}} header p{{margin:0;color:#c4d8e6}} section{{background:white;border:1px solid #d7e1e8;border-radius:14px;padding:22px 26px;margin-bottom:16px}}
    h2{{font-size:21px;margin:0 0 12px;color:#13577b}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:16px/1.5 ui-monospace,Menlo,monospace}}
    .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}} .rank{{background:#f5f8fa;padding:12px;margin:8px 0;border-radius:8px}} small{{color:#496b7c}}
    </style></head><body><main><header><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></header>{cards}</main></body></html>'''
    path.write_text(text, encoding='utf-8')
    subprocess.run(['node', str(ROOT/'screenshot.mjs'), str(path.resolve()), str(screenshot.resolve())], check=True, env=os.environ.copy(), timeout=90)


def pre(data):
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, indent=2)
    return '<pre>' + esc(data) + '</pre>'


def pi_summary(data):
    header = f"会话 ID：{data['session']['id']}\n工具调用：{' → '.join(data['tools'])}\n日志：{data['event_log']}"
    return pre(header) + '<h3>pi 的实际回答</h3>' + pre(data['answer'])


def record_summary(data):
    sources = '\n'.join(f"{s['path']}  |  SHA-256 {s['sha256']}" for s in data['sources'])
    header = f"记录 ID：{data['id']}    版本：{data['revision']}\n项目：{data['project']}\n数据库：{data['database_path']}"
    return pre(header) + '<h3>记忆内容</h3>' + pre(data['content']) + '<h3>来源及摘要</h3>' + pre(sources)


def generation_summary(data):
    rows = []
    for item in data['evidence']:
        rows.append(f"[{item['id']}] {item['source']} · {item['title']}\n{item['text']}")
    return pre(data['answer']) + '<h3>证据原文与来源</h3>' + pre('\n\n'.join(rows)) + f"<p><small>模型：{esc(data['model'])} · 引用编号有效：{esc(data['citation_ids_valid'])}</small></p>"


def comparison(data):
    cols = []
    for key, label in [('vector','向量检索'),('bm25','BM25 关键词'),('hybrid','RRF 混合检索')]:
        rows = []
        for i, item in enumerate(data[key], 1):
            rows.append(f'<div class="rank"><b>#{i} {esc(item["source"])} · {esc(item["title"])}</b><br><small>节点 {esc(item["id"][:16])} | 得分 {item["score"]:.5f}</small><br>{esc(item["text"][:210])}</div>')
        cols.append(f'<div><h3>{label}</h3>{"".join(rows)}</div>')
    return '<div class="grid">' + ''.join(cols) + '</div>'
