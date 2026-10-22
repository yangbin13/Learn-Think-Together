"""Exercise the actual export helpers in Chromium without starting a service."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(report_path):
    from playwright.sync_api import sync_playwright
    compile_script = '''
const fs = require('node:fs'); const ts = require('typescript');
const sources = ['mind-map', 'mind-map-export'].map(name => ts.transpileModule(
  fs.readFileSync('src/lib/' + name + '.ts', 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
).outputText); process.stdout.write(JSON.stringify(sources));
'''
    compiled = subprocess.run(['node', '-e', compile_script], cwd=ROOT / 'frontend',
                              check=True, capture_output=True, text=True, encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        report = page.evaluate('''async sources => {
            const map = {}, exporter = {};
            new Function('exports', sources[0])(map);
            new Function('exports', 'require', sources[1])(exporter, () => map);
            const nodes = [{ id: 'book', label: '离线复习格式验证', kind: 'document', children: ['section'], parent_id: null },
                { id: 'section', label: '揭示后才能看到的知识点<&>', children: [], parent_id: 'book', kind: 'section' }];
            const model = map.createMindMap({tree:{root_ids:['book'], nodes}}, '离线复习格式验证');
            const layout = map.layoutMindMap(model, 'book', new Set(['book']), false);
            const checks = [];
            function check(name, passed, detail) { checks.push({name, passed, detail}); }
            const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
            const active = new Set();
            URL.createObjectURL = blob => { const url = originalCreate.call(URL, blob); active.add(url); return url; };
            URL.revokeObjectURL = url => { active.delete(url); return originalRevoke.call(URL, url); };
            try {
                const normal = await exporter.mindMapPng(model, layout);
                const recalled = await exporter.mindMapPng(model, layout, true);
                const decoded = await createImageBitmap(normal);
                const svg = new DOMParser().parseFromString(map.mindMapSvg(model, layout), 'image/svg+xml').documentElement;
                check('actual PNG helper rasterizes a mobile layout at 2x', normal.type === 'image/png' &&
                    decoded.width === Number(svg.getAttribute('width')) * 2 &&
                    decoded.height === Number(svg.getAttribute('height')) * 2,
                    {width: decoded.width, height: decoded.height});
                decoded.close();
                const a = new Uint8Array(await normal.arrayBuffer()), b = new Uint8Array(await recalled.arrayBuffer());
                check('recall masking changes the rasterized image', a.length !== b.length || a.some((v,i) => v !== b[i]));
                let sizeError = '';
                try { await exporter.mindMapPng(model, {...layout, height: 20000}); }
                catch (error) { sizeError = error.message; }
                check('oversized layout refuses to reduce text below native size', sizeError.includes('当前展开的内容太多'));
                const originalToBlob = HTMLCanvasElement.prototype.toBlob;
                let encodeError = '';
                try {
                    HTMLCanvasElement.prototype.toBlob = function(callback) { callback(null); };
                    await exporter.mindMapPng(model, layout);
                } catch (error) { encodeError = error.message; }
                finally { HTMLCanvasElement.prototype.toBlob = originalToBlob; }
                check('encoding failure returns recovery instructions', encodeError.includes('保存失败') && encodeError.includes('重试'));
                const retry = await exporter.mindMapPng(model, layout);
                check('a successful retry works after an encoding failure', retry.type === 'image/png' && retry.size > 0);
                check('all temporary SVG object URLs are released after success and failure', active.size === 0);
            } finally { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
            return {engine: 'Chromium', scope: 'actual frontend export helpers; no API or service',
                physical_phone_tested: false, checks, passed: checks.every(check => check.passed)};
        }''', json.loads(compiled.stdout))
        browser.close()
    path = report_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for check in report['checks']:
        print(('PASS ' if check['passed'] else 'FAIL ') + check['name'])
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / 'data/validation/mindmap-export-browser.json')
    run(parser.parse_args().report)
