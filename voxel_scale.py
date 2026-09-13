"""Integer cell replication, including Teardown's dense TDCZ models.

No palette remapping, model splitting, or XML scale is used. TDCZ is an
x-fastest byte array, zlib compressed with 8 KiB sync flush boundaries.
"""
from localization import tr
from pathlib import Path
import math
import struct
import zlib
from vox_metadata import Reader, decode_rotation, IDENTITY3

def chunk(tag, data=b'', children=b''):
    return tag + struct.pack('<ii', len(data), len(children)) + data + children

def dictionary(values):
    out = struct.pack('<i', len(values))
    for key, value in values.items():
        for text in (key, value):
            raw = str(text).encode('utf-8')
            out += struct.pack('<i', len(raw)) + raw
    return out

def chunks(raw):
    if raw[:4] != b'VOX ' or raw[8:12] != b'MAIN':
        raise ValueError(tr('无效 VOX 文件'))
    result = []

    def walk(start, end):
        while start < end:
            if start + 12 > end:
                raise ValueError(tr('VOX 块头损坏'))
            tag = raw[start:start + 4]
            n, c = struct.unpack_from('<ii', raw, start + 4)
            a = start + 12
            b = a + n
            finish = b + c
            if min(n, c) < 0 or finish > end:
                raise ValueError(tr('VOX 数据截断'))
            if c:
                raise ValueError(tr('暂不支持带嵌套子块的 VOX 数据，已停止导出'))
            result.append((tag, raw[a:b]))
            start = finish
    n, c = struct.unpack_from('<ii', raw, 12)
    if 20 + n + c != len(raw):
        raise ValueError(tr('VOX 长度不匹配'))
    walk(20 + n, len(raw))
    return result

def read_models(entries, limit=512 * 1024 * 1024):
    models = []
    size = None
    for tag, data in entries:
        if tag == b'SIZE':
            size = struct.unpack('<iii', data)
        elif tag in (b'XYZI', b'TDCZ'):
            dims = struct.unpack_from('<iii', data) if tag == b'TDCZ' else size
            if not dims or min(dims) < 1 or math.prod(dims) > limit:
                raise ValueError(tr('模型体素画布过大或尺寸无效'))
            volume = math.prod(dims)
            if tag == b'TDCZ':
                decoder = zlib.decompressobj()
                dense = decoder.decompress(data[12:], volume + 1)
                if len(dense) != volume or decoder.unconsumed_tail:
                    raise ValueError(tr('TDCZ 解压尺寸不匹配'))
            else:
                count = struct.unpack_from('<i', data)[0]
                if count < 0 or len(data) != 4 + 4 * count:
                    raise ValueError(tr('XYZI 长度无效'))
                dense = bytearray(volume)
                sx, sy, sz = dims
                for i in range(4, len(data), 4):
                    x, y, z, color = data[i:i + 4]
                    if x >= sx or y >= sy or z >= sz:
                        raise ValueError(tr('XYZI 坐标超出 SIZE'))
                    dense[x + sx * (y + sy * z)] = color
            models.append((dims, dense))
            size = None
    if not models:
        raise ValueError(tr('VOX 没有可读取的模型'))
    return models

def nodes(entries):
    result = {}
    for tag, data in entries:
        if tag not in (b'nTRN', b'nGRP', b'nSHP'):
            continue
        r = Reader(data, 0, len(data))
        nid = r.i32()
        attrs = r.dictionary()
        if tag == b'nTRN':
            child, reserved, layer, count = (r.i32(), r.i32(), r.i32(), r.i32())
            value = (attrs, child, reserved, layer, [r.dictionary() for _ in range(count)])
        elif tag == b'nGRP':
            value = (attrs, [r.i32() for _ in range(r.i32())])
        else:
            value = (attrs, [(r.i32(), r.dictionary()) for _ in range(r.i32())])
        result[nid] = (tag, value)
    return result

def selected_model(entries, models, name):
    graph = nodes(entries)

    def direct(nid, seen=()):
        if nid in seen or nid not in graph:
            return None
        tag, value = graph[nid]
        if tag == b'nSHP':
            ids = {i for i, a in value[1]}
        elif tag == b'nGRP':
            ids = {direct(i, seen + (nid,)) for i in value[1]}
        else:
            return None
        return next(iter(ids)) if len(ids) == 1 and None not in ids else None
    matches = set()
    for nid, (tag, value) in graph.items():
        attrs = value[0]
        if tag == b'nTRN' and attrs.get('_name') == name:
            mid = direct(value[1])
            rot = decode_rotation(int((value[4] or [{}])[0].get('_r', '4')))
            if mid is not None:
                matches.add((mid, rot))
        elif tag == b'nSHP':
            for mid, a in value[1]:
                if a.get('_name') == name or (len(value[1]) == 1 and attrs.get('_name') == name):
                    matches.add((mid, IDENTITY3))
    if len(matches) != 1:
        raise ValueError(f"{tr('无法唯一解析 VOX object=')}{name}{tr('，已停止避免错位')}")
    return matches.pop()

def occupied_bounds(dims, dense):
    sx, sy, sz = dims
    lo = [sx, sy, sz]
    hi = [0, 0, 0]
    for z in range(sz):
        for y in range(sy):
            row = dense[sx * (y + sy * z):sx * (y + sy * z + 1)]
            left = len(row) - len(row.lstrip(b'\x00'))
            if left == sx:
                continue
            right = len(row.rstrip(b'\x00'))
            lo = [min(lo[0], left), min(lo[1], y), min(lo[2], z)]
            hi = [max(hi[0], right), max(hi[1], y + 1), max(hi[2], z + 1)]
    if hi[0] == 0:
        raise ValueError(tr('所选 VOX 对象没有实体体素'))
    return (tuple(lo), tuple(hi))

def selected_size(entries, models, name):
    mid, rot = selected_model(entries, models, name)
    dims, dense = models[mid]
    lo, hi = occupied_bounds(dims, dense)
    size = tuple((hi[i] - lo[i] for i in range(3)))
    return tuple((sum((abs(rot[i][j]) * size[j] for j in range(3))) for i in range(3)))

def selected_entries(entries, models, name):
    """Teardown object selection trims air before placing its integer pivot."""
    mid, rot = selected_model(entries, models, name)
    dims, dense = models[mid]
    lo, hi = occupied_bounds(dims, dense)
    size = tuple((hi[i] - lo[i] for i in range(3)))
    sx, sy, _ = dims
    cropped = b''.join((dense[lo[0] + sx * (y + sy * z):hi[0] + sx * (y + sy * z)] for z in range(lo[2], hi[2]) for y in range(lo[1], hi[1])))
    rotation = next((code for code in range(128) if code & 3 < 3 and code >> 2 & 3 < 3 and (code & 3 != code >> 2 & 3) and (decode_rotation(code) == rot)))
    attrs = {'_name': name}
    layer = -1
    for tag, value in nodes(entries).values():
        if tag == b'nTRN' and value[0].get('_name') == name:
            if len(value[4]) > 1:
                raise ValueError(tr('所选 VOX 对象含多帧变换，暂不能保证静态放大精度'))
            attrs = dict(value[0])
            layer = value[3]
    filtered = [(tag, data) for tag, data in entries if tag not in {b'PACK', b'SIZE', b'XYZI', b'TDCZ', b'nTRN', b'nGRP', b'nSHP'}]
    result = [(b'TDCZ', b'')] + filtered + [(b'nTRN', struct.pack('<i', 0) + dictionary(attrs) + struct.pack('<iiii', 1, -1, layer, 1) + dictionary({'_r': str(rotation)})), (b'nSHP', struct.pack('<i', 1) + dictionary({}) + struct.pack('<ii', 1, 0) + dictionary({}))]
    return (result, [(size, cropped)], {'canvas': list(dims), 'occupied_min': list(lo), 'occupied_size': list(size), 'object': name})

def replicate(dims, dense, k, check):
    sx, sy, sz = dims
    compressor = zlib.compressobj(6)
    output = []
    pending = bytearray()

    def push(data):
        pending.extend(data)
        while len(pending) >= 8192:
            output.append(compressor.compress(bytes(pending[:8192])))
            output.append(compressor.flush(zlib.Z_SYNC_FLUSH))
            del pending[:8192]
    table = [bytes([i]) * k for i in range(256)]
    for z in range(sz):
        check()
        plane = b''.join((b''.join((table[v] for v in dense[sx * (y + sy * z):sx * (y + sy * z + 1)])) * k for y in range(sy)))
        for _ in range(k):
            push(plane)
    if pending:
        output.append(compressor.compress(pending))
    output.append(compressor.flush(zlib.Z_SYNC_FLUSH))
    return struct.pack('<iii', *(d * k for d in dims)) + b''.join(output)

def scale_vox(source, target, k, scene=False, check=lambda: None, object_name=''):
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(tr('倍数必须是正整数'))
    raw = Path(source).read_bytes()
    entries = chunks(raw)
    models = read_models(entries)
    selection = None
    if object_name:
        entries, models, selection = selected_entries(entries, models, object_name)
    for dims, dense in models:
        if math.prod(dims) * k ** 3 > 2000000000:
            raise ValueError(f"{Path(source).name}{tr(' 放大后单个画布超过 20 亿体素；请降低倍数')}")
    graph = nodes(entries)
    fresh = max(graph, default=-1) + 1
    out = []
    mid = 0
    for tag, data in entries:
        check()
        if tag == b'SIZE':
            continue
        if tag in (b'XYZI', b'TDCZ'):
            dims, dense = models[mid]
            mid += 1
            out.append(chunk(b'SIZE', struct.pack('<iii', *(d * k for d in dims))))
            out.append(chunk(b'TDCZ', replicate(dims, dense, k, check)))
            continue
        if tag == b'nTRN':
            r = Reader(data, 0, len(data))
            nid = r.i32()
            attrs, child, reserved, layer, frames = graph[nid][1]
            converted = []
            for f in frames:
                f = dict(f)
                if '_t' in f:
                    f['_t'] = ' '.join((str(int(v) * k) for v in f['_t'].split()))
                converted.append(dictionary(f))
            data = struct.pack('<i', nid) + dictionary(attrs) + struct.pack('<iiii', child, reserved, layer, len(frames)) + b''.join(converted)
        elif tag == b'nSHP' and scene:
            nid = struct.unpack_from('<i', data)[0]
            refs = graph[nid][1][1]
            deltas = {tuple((d * k // 2 - k * (d // 2) for d in models[i][0])) for i, a in refs}
            if len(deltas) != 1:
                raise ValueError(tr('动画模型各帧旋转中心不同，暂不能安全放大'))
            delta = deltas.pop()
            if any(delta):
                out.append(chunk(b'nTRN', struct.pack('<i', nid) + dictionary({}) + struct.pack('<iiii', fresh, -1, -1, 1) + dictionary({'_t': ' '.join(map(str, delta))})))
                data = struct.pack('<i', fresh) + data[4:]
                fresh += 1
        out.append(chunk(tag, data))
    if scene and (not graph):
        if len(models) != 1:
            raise ValueError(tr('无场景图的多模型 VOX 无法确定布局'))
        delta = tuple((d * k // 2 - k * (d // 2) for d in models[0][0]))
        out.extend([chunk(b'nTRN', struct.pack('<i', 0) + dictionary({}) + struct.pack('<iiii', 1, -1, -1, 1) + dictionary({'_t': ' '.join(map(str, delta))})), chunk(b'nSHP', struct.pack('<i', 1) + dictionary({}) + struct.pack('<ii', 1, 0) + dictionary({}))])
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_bytes(raw[:8] + chunk(b'MAIN', children=b''.join(out)))
    return {'models': len(models), 'dimensions': [[d * k for d in dims] for dims, _ in models], 'format': 'TDCZ', 'factor': k, 'selection': selection}
