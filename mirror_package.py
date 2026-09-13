"""Create a self-contained, Teardown-usable mirrored XML/VOX package.

Reflection cannot be represented by an Euler rotation alone.  We use a fixed
local X reflection S at every hierarchy node.  For a source world reflection M:

    W_mirrored = M * W_source * S

The remaining local S is baked into each visual primitive.  VOX files are
rewritten (including model voxels and MagicaVoxel scene transforms); corner-
pivot voxboxes receive a compensating local translation.  All resulting XML
transforms are proper rotations, so they can be serialized as Teardown pos/rot.
"""
from __future__ import annotations
from localization import tr
from dataclasses import dataclass, field
from collections import Counter
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
from vox_metadata import named_object_mirror_x_shift

def identity():
    return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))

def matmul(a, b):
    return tuple((tuple((sum((a[r][k] * b[k][c] for k in range(4))) for c in range(4))) for r in range(4)))

def translation(x, y, z):
    return ((1.0, 0.0, 0.0, x), (0.0, 1.0, 0.0, y), (0.0, 0.0, 1.0, z), (0.0, 0.0, 0.0, 1.0))

def rigid_inverse(m):
    tx, ty, tz = (m[0][3], m[1][3], m[2][3])
    return ((m[0][0], m[1][0], m[2][0], -(m[0][0] * tx + m[1][0] * ty + m[2][0] * tz)), (m[0][1], m[1][1], m[2][1], -(m[0][1] * tx + m[1][1] * ty + m[2][1] * tz)), (m[0][2], m[1][2], m[2][2], -(m[0][2] * tx + m[1][2] * ty + m[2][2] * tz)), (0.0, 0.0, 0.0, 1.0))

def euler_matrix(rot):
    x, y, z = (math.radians(v) for v in rot)
    cx, sx, cy, sy, cz, sz = (math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z))
    return ((cy * cz, -cy * sz * cx + sy * sx, cy * sz * sx + sy * cx, 0.0), (sz, cz * cx, -cz * sx, 0.0), (-sy * cz, sy * sz * cx + cy * sx, -sy * sz * sx + cy * cx, 0.0), (0.0, 0.0, 0.0, 1.0))

def local_matrix(pos, rot):
    result = [list(row) for row in euler_matrix(rot)]
    result[0][3], result[1][3], result[2][3] = pos
    return tuple((tuple(row) for row in result))

def matrix_euler(m):
    """Inverse of Teardown R=Ry*Rz*Rx, returned in degrees."""
    sz = max(-1.0, min(1.0, m[1][0]))
    z = math.asin(sz)
    cz = math.cos(z)
    if abs(cz) > 1e-07:
        x = math.atan2(-m[1][2], m[1][1])
        y = math.atan2(-m[2][0], m[0][0])
    else:
        x = 0.0
        y = math.atan2(m[0][2], m[2][2])
    return tuple((math.degrees(v) for v in (x, y, z)))

def parse_vector(text, default=(0.0, 0.0, 0.0)):
    if not text or not text.strip():
        return tuple(default)
    tokens = [v for v in re.split('[,;\\s]+', text.strip()) if v]
    values = [parse_scalar(v, 0.0) for v in tokens[:3]]
    seed = list(default) + [0.0, 0.0, 0.0]
    values.extend(seed[len(values):3])
    return tuple(values[:3])

def parse_scalar(text, default=0.0):
    match = re.match('^[\\t ]*([+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?)', str(text or ''))
    if not match:
        return float(default)
    try:
        value = float(match.group(1))
    except ValueError:
        return float(default)
    return value if math.isfinite(value) else float(default)

def fmt(value):
    value = 0.0 if abs(value) < 5e-13 else value
    text = f'{value:.12f}'.rstrip('0').rstrip('.')
    return text if text not in {'', '-0'} else '0'

def vec_text(values):
    return ' '.join((fmt(v) for v in values))

def local_name(tag):
    return tag.rsplit('}', 1)[-1].lower()

def asset_suffix(value):
    return Path(split_vox_reference(value)[0].strip().rstrip(' .')).suffix.lower()

def split_vox_reference(value):
    raw = str(value or '')
    match = re.match('^(.*?\\.vox[ .]*):(.*)$', raw, re.I)
    return (match[1], match[2].strip()) if match else (raw, '')
LOCAL_X = ((-1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))

def axis_reflection(axis):
    values = [1.0, 1.0, 1.0, 1.0]
    values[{'X': 0, 'Y': 1, 'Z': 2}[axis.upper()]] = -1.0
    return tuple((tuple((values[r] if r == c else 0.0 for c in range(4))) for r in range(4)))

def _find_mod_root(start):
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / 'info.txt').is_file():
            return candidate
    return current

def _try_mod_root(start):
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / 'info.txt').is_file():
            return candidate
    return None

def resolve_path(xml_path, virtual, game_root):
    raw = split_vox_reference(virtual)[0].strip().replace('\\', '/')
    if not raw:
        return None
    direct = Path(raw)
    if direct.is_absolute():
        return direct
    upper = raw.upper()
    xml_path = Path(xml_path).resolve()
    if upper.startswith('MOD/'):
        return _find_mod_root(xml_path.parent) / raw[4:]
    if upper.startswith('LEVEL/'):
        return xml_path.parent / xml_path.stem / raw[6:]
    if upper.startswith('BUILT-IN/'):
        game = Path(game_root)
        candidates = (game / 'data' / 'built-in' / raw[9:], game / raw[9:])
        return next((p for p in candidates if p.exists()), candidates[0])
    local = xml_path.parent / raw
    return local if local.exists() else _find_mod_root(xml_path.parent) / raw

def _read_i32(data, cursor):
    return (struct.unpack_from('<i', data, cursor)[0], cursor + 4)

def _write_i32(value):
    return struct.pack('<i', int(value))

def _read_text(data, cursor):
    size, cursor = _read_i32(data, cursor)
    return (data[cursor:cursor + size].decode('utf-8', 'replace'), cursor + size)

def _write_text(value):
    encoded = str(value).encode('utf-8')
    return _write_i32(len(encoded)) + encoded

def _read_dict(data, cursor):
    count, cursor = _read_i32(data, cursor)
    result = {}
    for _ in range(count):
        key, cursor = _read_text(data, cursor)
        value, cursor = _read_text(data, cursor)
        result[key] = value
    return (result, cursor)

def _write_dict(values):
    return _write_i32(len(values)) + b''.join((_write_text(k) + _write_text(v) for k, v in values.items()))

def _rot3(code):
    a, b = (code & 3, code >> 2 & 3)
    if a > 2 or b > 2 or a == b:
        return None
    c = 3 - a - b
    signs = (-1 if code & 16 else 1, -1 if code & 32 else 1, -1 if code & 64 else 1)
    rows = [[0] * 3 for _ in range(3)]
    rows[0][a], rows[1][b], rows[2][c] = signs
    det = rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1]) - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0]) + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    return tuple((tuple(row) for row in rows)) if abs(det) == 1 else None
ROTATION_CODES = {matrix: code for code in range(128) if (matrix := _rot3(code)) is not None}

def _mirror_rotation(code):
    r = _rot3(code)
    if r is None:
        raise ValueError(f"{tr('Invalid VOX signed-permutation code: ')}{code}")
    s = ((-1, 0, 0), (0, 1, 0), (0, 0, 1))
    result = tuple((tuple((sum((s[i][k] * r[k][q] * s[q][j] for k in range(3) for q in range(3))) for j in range(3))) for i in range(3)))
    return ROTATION_CODES[result]

def _mirror_frame(frame):
    result = dict(frame)
    if '_t' in result:
        parts = result['_t'].split()
        if len(parts) == 3:
            try:
                result['_t'] = f'{-int(parts[0])} {int(parts[1])} {int(parts[2])}'
            except ValueError:
                pass
    if '_r' in result:
        result['_r'] = str(_mirror_rotation(int(result['_r'])))
    return result

def mirror_vox(source, destination, scene_pivot=False):
    data = Path(source).read_bytes()
    if len(data) < 20 or data[:4] != b'VOX ' or data[8:12] != b'MAIN':
        raise ValueError(f'Invalid VOX: {source}')
    main_content, main_children = struct.unpack_from('<ii', data, 12)
    start = 20 + main_content
    finish = start + main_children
    if main_content < 0 or main_children < 0 or finish != len(data):
        raise ValueError(f"{tr('Invalid VOX MAIN length: ')}{source}")
    voxel_chunks = 0

    def rewrite(begin, end):
        nonlocal voxel_chunks
        cursor, output, pending_size = (begin, bytearray(), None)
        while cursor + 12 <= end:
            kind = data[cursor:cursor + 4]
            size, child_size = struct.unpack_from('<ii', data, cursor + 4)
            c0, c1, c2 = (cursor + 12, cursor + 12 + size, cursor + 12 + size + child_size)
            if size < 0 or child_size < 0 or c2 > end:
                raise ValueError(tr('Truncated VOX chunk'))
            content = data[c0:c1]
            children = rewrite(c1, c2) if child_size else b''
            if kind == b'SIZE' and len(content) >= 12:
                if pending_size is not None:
                    raise ValueError(tr('VOX SIZE without XYZI'))
                pending_size = struct.unpack_from('<iii', content, 0)
                if any((n < 1 for n in pending_size)):
                    raise ValueError(f"{tr('Invalid uncompressed VOX dimensions: ')}{pending_size}")
                if scene_pivot and pending_size[0] % 2:
                    content = struct.pack('<iii', pending_size[0] + 1, *pending_size[1:]) + content[12:]
            elif kind == b'XYZI' and pending_size and (len(content) >= 4):
                if any((n > 256 for n in pending_size)):
                    raise ValueError(tr('XYZI dimensions exceed byte coordinates'))
                voxel_chunks += 1
                count = struct.unpack_from('<i', content, 0)[0]
                if count < 0 or len(content) != 4 + count * 4:
                    raise ValueError(tr('Invalid VOX XYZI voxel count'))
                mutable = bytearray(content)
                sx = pending_size[0]
                for index in range(count):
                    offset = 4 + index * 4
                    if any((mutable[offset + j] >= pending_size[j] for j in range(3))):
                        raise ValueError(tr('VOX voxel lies outside SIZE'))
                    mutable[offset] = sx - 1 - mutable[offset]
                content = bytes(mutable)
                pending_size = None
            elif kind == b'XYZI':
                raise ValueError(tr('VOX XYZI without SIZE'))
            elif kind == b'TDCZ':
                from voxel_scale import read_models, replicate
                dims, dense = read_models([(kind, content)])[0]
                sx, sy, sz = dims
                padding = 1 if scene_pivot and sx % 2 else 0
                mirrored = bytearray()
                for start in range(0, len(dense), sx):
                    mirrored.extend(dense[start:start + sx][::-1])
                    if padding:
                        mirrored.append(0)
                content = replicate((sx + padding, sy, sz), mirrored, 1, lambda: None)
                voxel_chunks += 1
                pending_size = None
            elif kind == b'nTRN':
                p = 0
                node, p = _read_i32(content, p)
                attrs, p = _read_dict(content, p)
                child, p = _read_i32(content, p)
                reserved, p = _read_i32(content, p)
                layer, p = _read_i32(content, p)
                count, p = _read_i32(content, p)
                frames = []
                for _ in range(count):
                    frame, p = _read_dict(content, p)
                    frames.append(_mirror_frame(frame))
                content = _write_i32(node) + _write_dict(attrs) + _write_i32(child) + _write_i32(reserved) + _write_i32(layer) + _write_i32(count) + b''.join((_write_dict(frame) for frame in frames))
            output += kind + struct.pack('<ii', len(content), len(children)) + content + children
            cursor = c2
        if cursor != end:
            raise ValueError(tr('Invalid VOX chunk alignment'))
        if pending_size is not None:
            raise ValueError(tr('VOX SIZE without XYZI'))
        return bytes(output)
    children = rewrite(start, finish)
    if voxel_chunks == 0:
        raise ValueError(f"{tr('No readable XYZI data in VOX (compressed/unsupported file): ')}{source}")
    rebuilt = data[:8] + b'MAIN' + struct.pack('<ii', main_content, len(children)) + data[20:20 + main_content] + children
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rebuilt)

@dataclass
class MirrorReport:
    source_xml: str
    output_xml: str
    axis: str
    vox_files: list[str] = field(default_factory=list)
    instance_xml: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tags_verified: bool = True

    def as_dict(self):
        return {'source_xml': self.source_xml, 'output_xml': self.output_xml, 'axis': self.axis, 'vox_files': self.vox_files, 'instance_xml': self.instance_xml, 'warnings': self.warnings, 'tags_verified': self.tags_verified}

class MirrorPackageBuilder:

    def __init__(self, source_xml, output_xml, axis, game_root=''):
        self.source_xml = Path(source_xml).resolve()
        self.output_xml = Path(output_xml).resolve()
        self.axis = axis.upper()
        self.game_root = game_root
        self.assets = self.output_xml.parent / (self.output_xml.stem + '_assets')
        self.report = MirrorReport(str(self.source_xml), str(self.output_xml), self.axis)
        self.vox_cache = {}
        self.xml_cache = {}
        self.active = set()
        self.vox_pivot_cache = {}
        self._stage = None
        self._warned_kinds = set()

    def _named_vox_pivot_correction(self, source_xml, effective):
        """Compensate Teardown's integer-centre pivot after VOX mirroring.

        Named ``object=`` models are rebased and pivoted at floor(size/2) on
        X/Y.  Reflecting an odd local-X size changes that pivot by one voxel;
        an Euler/position mirror alone cannot absorb it without this geometry-
        aware correction.
        """
        object_name = (effective.get('object') or '').strip()
        virtual = (effective.get('file') or '').strip()
        if not object_name or asset_suffix(virtual) != '.vox':
            return 0.0
        resolved = resolve_path(source_xml, virtual, self.game_root)
        if not resolved or not resolved.is_file():
            return 0.0
        key = (os.path.normcase(str(resolved.resolve())), object_name)
        if key not in self.vox_pivot_cache:
            try:
                self.vox_pivot_cache[key] = named_object_mirror_x_shift(resolved, object_name)
            except Exception as exc:
                self.vox_pivot_cache[key] = None
                self.report.warnings.append(f"{tr('VOX pivot metadata failed: ')}{virtual}:{object_name}: {exc}")
        shift = self.vox_pivot_cache[key]
        if shift is None:
            raise ValueError(f"{tr('Cannot safely resolve VOX object pivot: ')}{virtual}:{object_name}")
        pitch = 0.1 * parse_scalar(effective.get('scale', '1'), 1.0)
        return float(shift) * pitch

    def _virtual_ref(self, target, destination_xml):
        """Return a path Teardown resolves from the generated XML.

        Teardown assets inside a mod must be addressed from the mod root with
        the ``MOD/`` prefix.  A plain path relative to the current XML works in
        our offline importer but is not a valid resource address in many game
        loading contexts.  Generated nested instance XML uses the same stable
        virtual path, so it cannot accidentally resolve relative to the
        ``assets/xml`` folder.
        """
        target = Path(target).resolve()
        destination_xml = Path(destination_xml).resolve()
        if self._stage is not None:
            if target.is_relative_to(self._stage):
                target = self.output_xml.parent / target.relative_to(self._stage)
            if destination_xml.is_relative_to(self._stage):
                destination_xml = self.output_xml.parent / destination_xml.relative_to(self._stage)
        mod_root = _try_mod_root(destination_xml.parent)
        if mod_root is not None:
            try:
                return 'MOD/' + target.relative_to(mod_root).as_posix()
            except ValueError:
                pass
        game_root = Path(self.game_root).resolve() if self.game_root else None
        if game_root is not None:
            candidates = (game_root / 'data' / 'built-in', game_root / 'built-in')
            for built_in in candidates:
                if not built_in.is_dir():
                    continue
                try:
                    return 'BUILT-IN/' + target.relative_to(built_in).as_posix()
                except ValueError:
                    pass
        return Path(os.path.relpath(target, destination_xml.parent)).as_posix()

    def build(self):
        if self.axis not in {'X', 'Y', 'Z'}:
            raise ValueError(tr('Mirror axis must be X, Y or Z'))
        if self.source_xml == self.output_xml:
            raise ValueError(tr('Output must not overwrite source XML'))
        if self.source_xml.is_relative_to(self.assets):
            raise ValueError(tr('Source XML is inside the output assets directory'))
        self.output_xml.parent.mkdir(parents=True, exist_ok=True)
        final_assets = self.assets
        with tempfile.TemporaryDirectory(prefix='.td-mirror-', dir=self.output_xml.parent) as stage:
            self._stage = Path(stage).resolve()
            self.assets = self._stage / final_assets.name
            self.assets.mkdir()
            staged_xml = self._stage / self.output_xml.name
            self._process_xml(self.source_xml, staged_xml, axis_reflection(self.axis), top=True)
            self._verify_tags(self.source_xml, staged_xml)
            backups = []
            installed = []
            try:
                for dst in (final_assets, self.output_xml):
                    if dst.exists():
                        bak = self._stage / ('previous-' + dst.name)
                        os.replace(dst, bak)
                        backups.append((dst, bak))
                for src, dst in ((self.assets, final_assets), (staged_xml, self.output_xml)):
                    os.replace(src, dst)
                    installed.append(dst)
            except BaseException:
                for dst in reversed(installed):
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    elif dst.exists():
                        dst.unlink()
                for dst, bak in reversed(backups):
                    os.replace(bak, dst)
                raise
            for field in ('vox_files', 'instance_xml'):
                setattr(self.report, field, [str(self.output_xml.parent / Path(p).relative_to(self._stage)) for p in getattr(self.report, field)])
        self.assets = final_assets
        self._stage = None
        return self.report

    def _asset_name(self, source, kind):
        digest = hashlib.sha1(os.path.normcase(str(Path(source).resolve())).encode('utf-8')).hexdigest()[:10]
        clean = Path(str(source).rstrip(' .'))
        return f'{clean.stem}_{kind}_{digest}{clean.suffix.lower()}'

    def _mirror_vox_ref(self, source_xml, value, destination_xml, scene_pivot=False):
        resolved = resolve_path(source_xml, value, self.game_root)
        if not resolved or not resolved.is_file():
            raise FileNotFoundError(f'VOX not found: {value} in {source_xml}')
        key = (os.path.normcase(str(resolved.resolve())), scene_pivot)
        if key not in self.vox_cache:
            variant = 'mirror_scene_x' if scene_pivot else 'mirror_local_x'
            target = self.assets / 'vox' / self._asset_name(resolved, variant)
            mirror_vox(resolved, target, scene_pivot)
            self.vox_cache[key] = target
            self.report.vox_files.append(str(target))
        return self._virtual_ref(self.vox_cache[key], destination_xml)

    def _instance_ref(self, source_xml, value, destination_xml):
        resolved = resolve_path(source_xml, value, self.game_root)
        if not resolved or not resolved.is_file():
            raise FileNotFoundError(f"{tr('Instance XML not found: ')}{value} in {source_xml}")
        key = os.path.normcase(str(resolved.resolve()))
        if key in self.active:
            raise ValueError(f"{tr('Recursive instance cycle: ')}{resolved}")
        if key not in self.xml_cache:
            target = self.assets / 'xml' / self._asset_name(resolved, 'mirror_local_x')
            self.xml_cache[key] = target
            self._process_xml(resolved, target, LOCAL_X, top=False)
            self.report.instance_xml.append(str(target))
        return self._virtual_ref(self.xml_cache[key], destination_xml)

    def _process_xml(self, source, destination, root_reflection, top):
        key = os.path.normcase(str(Path(source).resolve()))
        if key in self.active:
            self.report.warnings.append(f"{tr('Instance cycle skipped: ')}{source}")
            return
        raw = Path(source).read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError(tr('DTD/entities are not supported'))
        root = ET.fromstring(raw, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)))
        if hasattr(self, "_prepare_document"):
            self._prepare_document(root, source)
        self.active.add(key)
        try:
            wrapper = local_name(root.tag) in {'scene', 'prefab'}
            elements = [node for node in root if isinstance(node.tag, str)] if wrapper else [root]
            for element in elements:
                prefab_root = local_name(root.tag) == 'prefab' and len(elements) == 1 and (local_name(element.tag) == 'group') and element.attrib.get('name', '').lower().startswith('instance=')
                self._walk(element, Path(source), Path(destination), root_reflection, identity(), {}, root_node=True, suppress_transform=prefab_root)
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            tree = ET.ElementTree(root)
            try:
                ET.indent(tree, space='  ')
            except AttributeError:
                pass
            tree.write(destination, encoding='utf-8', xml_declaration=True)
            self._verify_tags(source, destination)
        finally:
            self.active.remove(key)

    def _walk(self, element, source_xml, destination_xml, root_reflection, parent_correction, inherited, root_node=False, suppress_transform=False):
        if not isinstance(element.tag, str):
            return
        kind = local_name(element.tag)
        if kind in {'joint', 'wheel', 'voxscript', 'script'} and kind not in self._warned_kinds:
            self._warned_kinds.add(kind)
            self.report.warnings.append({'joint': tr('Joint positions are reflected; motor directions and angle limits need in-game verification.'), 'wheel': tr('Wheel positions are reflected; steering and suspension behavior need in-game verification.'), 'voxscript': tr('Runtime voxscript geometry is not baked or reflected by this static XML tool.'), 'script': tr('Lua code and runtime-created geometry are unchanged; verify scripted behavior in-game.')}[kind])
        explicit = {local_name(k): v for k, v in element.attrib.items()}
        effective = dict(inherited)
        effective.update(explicit)
        pos = parse_vector(effective.get('pos'))
        rot = parse_vector(effective.get('rot'))
        original = identity() if suppress_transform else local_matrix(pos, rot)
        size = parse_vector(effective.get('size'), (50.0, 30.0, 20.0)) if 'size' not in effective else parse_vector(effective.get('size'), (1.0, 1.0, 1.0))
        correction = identity()
        if kind in {'voxbox', 'box'}:
            from geometry_values import box_size
            size = box_size(effective.get('size', '50 30 20'))
            pitch = 0.1 * parse_scalar(effective.get('scale', '1'), 1.0)
            correction = translation(-size[0] * pitch, 0.0, 0.0)
        elif kind == 'vox':
            correction = translation(self._named_vox_pivot_correction(source_xml, effective), 0.0, 0.0)
        if suppress_transform:
            correction = identity()
            output_local = identity()
        else:
            base = matmul(matmul(root_reflection if root_node else LOCAL_X, original), LOCAL_X)
            output_local = matmul(matmul(rigid_inverse(parent_correction), base), correction)
        element.set('pos', vec_text((output_local[0][3], output_local[1][3], output_local[2][3])))
        element.set('rot', vec_text(matrix_euler(output_local)))
        if kind == 'vox' and asset_suffix(effective.get('file')) == '.vox':
            element.set('file', self._mirror_vox_ref(source_xml, effective['file'], destination_xml, scene_pivot=not bool(effective.get('object', '').strip())))
        if kind in {'voxbox', 'box'}:
            brush = effective.get('brush', '')
            if brush and brush.lower() != 'hole' and (asset_suffix(brush) == '.vox'):
                element.set('brush', self._mirror_vox_ref(source_xml, brush, destination_xml))
                offset = parse_vector(effective.get('offset'))
                element.set('offset', vec_text((size[0] - offset[0], offset[1], offset[2])))
        if kind in {'voxagon', 'trigger', 'water', 'boundary'}:
            vertex_entries = [(index, child) for index, child in enumerate(list(element)) if isinstance(child.tag, str) and local_name(child.tag) == 'vertex']
            for _, vertex in vertex_entries:
                raw_pos = (vertex.get('pos') or '').strip()
                tokens = [v for v in re.split('[,;\\s]+', raw_pos) if v]
                if len(tokens) >= 2:
                    x = parse_scalar(tokens[0], 0.0)
                    z = parse_scalar(tokens[1], 0.0)
                    suffix = [parse_scalar(value, 0.0) for value in tokens[2:]]
                    vertex.set('pos', vec_text((-x, z, *suffix)))
            if len(vertex_entries) >= 3:
                for _, vertex in vertex_entries:
                    element.remove(vertex)
                for (index, _), vertex in zip(vertex_entries, reversed([v for _, v in vertex_entries])):
                    element.insert(index, vertex)
            brush = effective.get('brush', '') if kind == 'voxagon' else ''
            if brush and brush.lower() != 'hole' and (asset_suffix(brush) == '.vox'):
                element.set('brush', self._mirror_vox_ref(source_xml, brush, destination_xml))
                offset = parse_vector(effective.get('offset'))
                element.set('offset', vec_text((-offset[0], offset[1], offset[2])))
        if kind == 'instance' and asset_suffix(effective.get('file')) in {'.xml', '.prefab'}:
            element.set('file', self._instance_ref(source_xml, effective['file'], destination_xml))
        child_inherited = inherited
        if kind == 'group':
            child_inherited = dict(inherited)
            props = []
            for name, value in explicit.items():
                match = re.fullmatch('prop(\\d+)', name, re.I)
                if match and '=' in value:
                    k, v = value.split('=', 1)
                    props.append((int(match.group(1)), k.strip().lower(), v.strip()))
            for _, name, value in sorted(props):
                child_inherited[name] = value
        for child in list(element):
            if not isinstance(child.tag, str):
                continue
            if local_name(child.tag) == 'vertex':
                continue
            self._walk(child, source_xml, destination_xml, root_reflection, correction, child_inherited, root_node=suppress_transform)

    def _verify_tags(self, source, output):

        def tags(path):
            raw = Path(path).read_bytes()
            root = ET.fromstring(raw)
            return Counter(((local_name(node.tag), node.attrib.get('tags')) for node in root.iter() if 'tags' in node.attrib))
        original, mirrored = (tags(source), tags(output))
        matched = original == mirrored
        self.report.tags_verified = self.report.tags_verified and matched
        if not matched:
            raise RuntimeError(tr('Tag verification failed; mirrored XML was not accepted'))

def build_mirror_package(source_xml, output_xml, axis, game_root=''):
    return MirrorPackageBuilder(source_xml, output_xml, axis, game_root).build().as_dict()
