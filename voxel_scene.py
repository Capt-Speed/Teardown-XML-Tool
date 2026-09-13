"""Bake XML geometry in VOX scene coordinates; keep every model <= 256.

The engine's legacy VOX reader expands TDCZ into byte-coordinate cells.
A large file therefore needs multiple ordinary models, not one large TDCZ.
Scene transforms rotate integer cell indices before adding the cell centre.
"""
from localization import tr
from pathlib import Path
import itertools, math, struct
from voxel_scale import chunks, read_models, nodes, occupied_bounds, chunk, dictionary
from vox_metadata import IDENTITY3, decode_rotation

def mv(r, v):
    return tuple((sum((r[i][j] * v[j] for j in range(3))) for i in range(3)))

def mm(a, b):
    return tuple((tuple((sum((a[i][k] * b[k][j] for k in range(3))) for j in range(3))) for i in range(3)))

def plus(a, b):
    return tuple((a[i] + b[i] for i in range(3)))

def scene_cell_bias(r):
    return tuple(((sum(r[i]) - (1, -1, 1)[i]) // 2 for i in range(3)))

def instances(entries, models):
    graph = nodes(entries)
    found = []
    layers = {}
    from vox_metadata import Reader
    for tag, data in entries:
        if tag == b'LAYR':
            r = Reader(data, 0, len(data))
            lid = r.i32()
            layers[lid] = r.dictionary()
    if not graph:
        if len(models) != 1:
            raise ValueError(tr('无场景图的多模型 VOX 不能确定布局'))
        return [{'mid': 0, 'r': IDENTITY3, 't': (0, 0, 0), 'name': '', 'hidden': False, 'ref': False}]
    childids = set()
    for tag, v in graph.values():
        childids.update([v[1]] if tag == b'nTRN' else v[1] if tag == b'nGRP' else [])
    roots = [i for i in graph if i not in childids]
    if not roots:
        raise ValueError(tr('VOX 场景图循环引用'))

    def walk(nid, r, t, name='', hidden=False, reference=False, path=()):
        if nid in path or nid not in graph:
            raise ValueError(tr('VOX 场景图损坏或循环引用'))
        tag, v = graph[nid]
        attrs = v[0]
        name = attrs.get('_name') or name
        hidden = hidden or attrs.get('_hidden') == '1'
        if tag == b'nTRN':
            if len(v[4]) > 1:
                raise ValueError(tr('VOX 含动画帧，不能按静态几何安全改写'))
            f = (v[4] or [{}])[0]
            lr = decode_rotation(int(f.get('_r', '4')))
            lt = tuple((int(n) for n in f.get('_t', '0 0 0').split()))
            if len(lt) != 3:
                raise ValueError(tr('VOX 平移不是三个整数'))
            layer = layers.get(v[3], {})
            walk(v[1], mm(r, lr), plus(t, mv(r, lt)), name, hidden or layer.get('_hidden') == '1', reference or layer.get('_name') == '$REF', path + (nid,))
        elif tag == b'nGRP':
            for c in v[1]:
                walk(c, r, t, name, hidden, reference, path + (nid,))
        else:
            if len(v[1]) != 1:
                raise ValueError(tr('VOX shape 含多帧模型，不能静态改写'))
            mid, a = v[1][0]
            if not 0 <= mid < len(models):
                raise ValueError(tr('VOX 引用了不存在的模型'))
            found.append({'mid': mid, 'r': r, 't': t, 'name': a.get('_name') or name, 'hidden': hidden, 'ref': reference})
    for root in roots:
        walk(root, IDENTITY3, (0, 0, 0))
    return found

def geometry_parts(entries, models, name=''):
    items = instances(entries, models)
    selected = [i for i in items if i['name'] == name] if name else [i for i in items if not i['hidden']]
    if not selected:
        raise ValueError(f"{tr('VOX 找不到对象：')}{name or tr('可见场景')}")
    result = []
    refs = {}
    for item in selected:
        dims, dense = models[item['mid']]
        try:
            lo, hi = occupied_bounds(dims, dense)
        except ValueError:
            continue
        center = tuple((d // 2 for d in dims))
        corners = [plus(mv(item['r'], tuple((v[j] - center[j] for j in range(3)))), item['t']) for v in itertools.product(*[(lo[j], hi[j] - 1) for j in range(3)])]
        low = tuple((min((v[j] for v in corners)) for j in range(3)))
        high = tuple((max((v[j] for v in corners)) + 1 for j in range(3)))
        if item['ref']:
            refs[item['name']] = (low, high)
            continue
        result.append({**item, 'lo': lo, 'hi': hi, 'low': low, 'high': high, 'center': center})
    if not result:
        raise ValueError(tr('VOX 对象没有实体体素'))
    for item in result:
        low, high = refs.get(item['name'], (item['low'], item['high']))
        item['pivot'] = ((low[0] + high[0]) // 2, (low[1] + high[1]) // 2, low[2]) if name else (0, 0, 0)
    return result

def bake_scene(source, target, k=1, object_name='', mirror_x=False, check=lambda: None):
    if not isinstance(k, int) or k < 1:
        raise ValueError(tr('倍数必须为正整数'))
    raw = Path(source).read_bytes()
    entries = chunks(raw)
    models = read_models(entries)
    parts = geometry_parts(entries, models, object_name)
    metadata = [chunk(t, d) for t, d in entries if t not in {b'PACK', b'SIZE', b'XYZI', b'TDCZ', b'nTRN', b'nGRP', b'nSHP', b'LAYR'}]
    outmodels = []
    placements = []
    total = 0
    bounds = []
    for part in parts:
        check()
        dims, dense = models[part['mid']]
        sx, sy, sz = dims
        r = part['r']
        lo, hi = (part['lo'], part['hi'])
        shape = tuple(((part['high'][j] - part['low'][j]) * k for j in range(3)))
        if math.prod(shape) > 2000000000:
            raise ValueError(tr('放大后的单个部件超过 20 亿体素'))
        bias = (0, 0, 0) if object_name else scene_cell_bias(r)
        origin = tuple(((part['low'][j] - part['pivot'][j] + bias[j]) * k for j in range(3)))
        if mirror_x:
            origin = (-origin[0] - shape[0], origin[1], origin[2])
        oriented_size = tuple((n // k for n in shape))
        ox, oy, oz = oriented_size
        oriented = bytearray(ox * oy * oz)
        for z in range(lo[2], hi[2]):
            check()
            for y in range(lo[1], hi[1]):
                for x in range(lo[0], hi[0]):
                    color = dense[x + sx * (y + sy * z)]
                    if not color:
                        continue
                    p = plus(mv(r, (x - part['center'][0], y - part['center'][1], z - part['center'][2])), part['t'])
                    a, b, c = (p[j] - part['low'][j] for j in range(3))
                    if mirror_x:
                        a = ox - 1 - a
                    oriented[a + ox * (b + oy * c)] = color
        for tz in range(0, shape[2], 256):
            for ty in range(0, shape[1], 256):
                for tx in range(0, shape[0], 256):
                    check()
                    td = tuple((min(256, shape[j] - v) for j, v in enumerate((tx, ty, tz))))
                    cells = bytearray()
                    mins = [256] * 3
                    maxs = [-1] * 3
                    for z in range(td[2]):
                        srcz = (tz + z) // k
                        for y in range(td[1]):
                            start = ox * ((ty + y) // k + oy * srcz)
                            for x in range(td[0]):
                                color = oriented[(tx + x) // k + start]
                                if color:
                                    cells.extend((x, y, z, color))
                                    for j, v in enumerate((x, y, z)):
                                        mins[j] = min(mins[j], v)
                                        maxs[j] = max(maxs[j], v)
                    if not cells:
                        continue
                    actual = tuple((maxs[j] - mins[j] + 1 for j in range(3)))
                    for i in range(0, len(cells), 4):
                        for j in range(3):
                            cells[i + j] -= mins[j]
                    mid = len(outmodels)
                    count = len(cells) // 4
                    total += count
                    outmodels.append(chunk(b'SIZE', struct.pack('<iii', *actual)) + chunk(b'XYZI', struct.pack('<i', count) + cells))
                    p = tuple((origin[j] + v + mins[j] + actual[j] // 2 - (0, 1, 0)[j] for j, v in enumerate((tx, ty, tz))))
                    placements.append((p, actual, part['name'] or 'part'))
        bounds.append({'model': part['mid'], 'name': part['name'], 'dimensions': list(shape), 'origin': list(origin)})
    graph = [chunk(b'nTRN', struct.pack('<i', 0) + dictionary({}) + struct.pack('<iiii', 1, -1, -1, 1) + dictionary({})), chunk(b'nGRP', struct.pack('<i', 1) + dictionary({}) + struct.pack('<i', len(placements)) + b''.join((struct.pack('<i', 2 + i * 2) for i in range(len(placements)))))]
    for i, (p, d, name) in enumerate(placements):
        graph.extend([chunk(b'nTRN', struct.pack('<i', 2 + i * 2) + dictionary({'_name': name}) + struct.pack('<iiii', 3 + i * 2, -1, -1, 1) + dictionary({'_t': ' '.join(map(str, p))})), chunk(b'nSHP', struct.pack('<i', 3 + i * 2) + dictionary({}) + struct.pack('<ii', 1, i) + dictionary({}))])
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw[:8] + chunk(b'MAIN', children=b''.join(outmodels + metadata + graph)))
    return {'models': len(outmodels), 'dimensions': [list(d) for p, d, n in placements], 'format': 'XYZI scene (tiles <=256)', 'factor': k, 'selection': {'object': object_name, 'parts': bounds}, 'output_voxels': total, 'mirror_x': mirror_x, 'xml_object': ''}
