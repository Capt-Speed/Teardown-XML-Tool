"""Read-only, independent per-cell verification for a completed export."""
from localization import tr
import json, hashlib
from pathlib import Path
from collections import Counter
from engine_reference import signature, read

def verify_export(report_path, progress=lambda message: None):
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    results = []
    for asset in report['vox']:
        source = Path(asset['source'])
        out = report_path.parent / asset['output']
        name = asset.get('selection', {}).get('object', '')
        k = asset['factor']
        progress(tr('核对体素与材质：') + source.name + ' / ' + name)
        a, ma = signature(source, name)
        b, mb = signature(out, divisor=k)
        expected = Counter({(-x - 1, y, z, c) if asset.get('mirror_x') else (x, y, z, c): n * k ** 3 for (x, y, z, c), n in a.items()})
        models, _, _ = read(out)
        errors = []
        if ma != mb:
            errors.append('palette/material metadata changed')
        if b != expected:
            errors.append('voxel positions, colors or multiplicity differ')
        if any((max(m.size) > 256 for m in models)):
            errors.append('model exceeds byte coordinates')
        results.append({'object': name, 'source': str(source), 'output': asset['output'], 'source_voxels': sum(a.values()), 'output_voxels': sum(b.values()), 'tiles': len(models), 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(), 'passed': not errors, 'errors': errors})
    sources = []
    for file, digest in report.get('source_hashes', {}).items():
        path = Path(file)
        same = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        sources.append({'file': file, 'unchanged': same})
    return {'ok': all((r['passed'] for r in results)) and all((s['unchanged'] for s in sources)), 'scope': 'offline per-cell geometry and materials; not an in-game physics test', 'assets': results, 'sources': sources}
