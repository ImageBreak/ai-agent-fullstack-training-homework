"""A separate process for each build and retrieval, exercising persisted indexes."""
import argparse
import os
from pathlib import Path
import rag

p = argparse.ArgumentParser()
p.add_argument('action', choices=['build','query'])
p.add_argument('--corpus', type=Path, required=True)
p.add_argument('--storage', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
if a.action == 'build':
    output = rag.build(a.corpus, a.storage)
else:
    output = rag.retrieve(a.storage)
    output['generation'] = rag.generate(output, os.environ)
rag.save_json(a.output, output)
print(f'{a.action}: {a.output}')
