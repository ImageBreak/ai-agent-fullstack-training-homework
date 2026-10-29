import shutil
from pathlib import Path

import rag
from llama_index.core.embeddings import MockEmbedding

FIXTURE = Path(__file__).resolve().parents[1] / 'fixture/login_demo/materials'


def test_rrf_deduplicates_and_uses_both_ranks():
    a, b, c = ({'id': x} for x in 'abc')
    scores = dict(rag.rrf([[a,b],[b,c]]))
    assert scores['b'] == 1/62 + 1/61
    assert scores['b'] > scores['a']


def test_index_persistence_and_update(tmp_path):
    corpus = tmp_path / 'corpus'
    shutil.copytree(FIXTURE, corpus)
    storage = tmp_path / 'storage'
    model = MockEmbedding(embed_dim=8)
    before = rag.build(corpus, storage, model)
    first = rag.retrieve(storage, model=model)
    assert before['node_count'] >= 5
    assert first['vector'] and first['bm25'] and first['hybrid']
    assert any('30 分钟' in item['text'] for item in first['bm25'])
    changed = rag.update_corpus(corpus)
    assert len(changed) == 5
    after = rag.build(corpus, storage, model)
    second = rag.retrieve(storage, model=model)
    assert before['version'] != after['version']
    removed = set(first['node_ids']) - set(second['node_ids'])
    assert removed and not removed.intersection(second['node_ids'])
    assert any('20 分钟' in item['text'] for item in second['bm25'])
    assert '支付会话有效期为 5 分钟' in (corpus / 'payment-session.md').read_text()
    assert '匿名浏览会话,120 分钟' in (corpus / 'session-matrix.csv').read_text()
    assert '记住我令牌,30 天' in (corpus / 'session-matrix.csv').read_text()


def test_generated_citations_use_evidence_ids_not_node_ids(monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs['messages'][1]['content']
            assert '"id": "E1"' in prompt
            assert '"node_id": "original-node"' in prompt
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='有效期 30 分钟 [E1]'))], usage=None)

    fake = SimpleNamespace(OpenAI=lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setitem(sys.modules, 'openai', fake)
    result = {'question': rag.QUESTION, 'hybrid': [{'id': 'original-node', 'source': 'session-rules.md', 'title': '有效期', 'text': '30 分钟', 'score': 1}]}
    output = rag.generate(result, {'HARNESS_API_KEY':'test', 'HARNESS_BASE_URL':'http://localhost', 'HARNESS_MODEL':'test'})
    assert output['citation_ids_valid']
    assert output['evidence'][0]['id'] == 'E1'
    assert output['evidence'][0]['node_id'] == 'original-node'
