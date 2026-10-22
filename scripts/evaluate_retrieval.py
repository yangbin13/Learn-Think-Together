"""Reproducible local seed retrieval smoke set; no provider requests or invented metrics."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import Settings
from backend.app.db import Database
from backend.app.ingestion import seed_catalog
from backend.app.retrieval import Retriever

class NoNetworkProvider:
    async def embed(self, texts):
        raise RuntimeError('Evaluation forbids provider calls')

async def main():
    cases = json.loads((ROOT/'content/evaluation/retrieval.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(root=ROOT, data_dir=Path(directory), embedding_enabled=False)
        db = Database(settings.database)
        seed_catalog(db, settings)
        retriever = Retriever(db, NoNetworkProvider())
        results = []
        for case in cases:
            started = time.perf_counter()
            found = await retriever.search(case['course_id'], 'local-evaluation', case['query'], 5)
            hits = found['items']
            matches = [i+1 for i,h in enumerate(hits) if case['expected'] in h['section'] or case['expected'] in h['text']]
            results.append({**case, 'hit_at_5': bool(matches), 'rank': matches[0] if matches else None,
                            'latency_ms': round((time.perf_counter()-started)*1000,2), 'method': found['method'],
                            'retrieved': [{'title':h['title'],'section':h['section'],'page':h['page']} for h in hits]})
        values = sorted(r['latency_ms'] for r in results)
        report = {'scope':'24 curated queries against local seed content; BM25 only, not general accuracy or learning outcome',
                  'count':len(results),'hits_at_5':sum(r['hit_at_5'] for r in results),
                  'hit_rate_at_5':sum(r['hit_at_5'] for r in results)/len(results),
                  'median_ms':values[len(values)//2], 'p95_ms':values[min(len(values)-1, int(len(values)*0.95))], 'results':results}
        output = ROOT/'data/validation/retrieval-results.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))

if __name__=='__main__': asyncio.run(main())
