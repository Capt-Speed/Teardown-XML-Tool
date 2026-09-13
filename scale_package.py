"""Export a sibling XML and only its transformed dependencies, transactionally."""
from localization import tr
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from fractions import Fraction
from math import lcm
from contextlib import contextmanager
import xml.etree.ElementTree as ET
from mirror_package import resolve_path, _try_mod_root, asset_suffix, euler_matrix, fmt, split_vox_reference
from voxel_scale import chunks, read_models, selected_size, scale_vox
from voxel_scene import bake_scene
from geometry_values import box_size

class Cancelled(Exception):
    pass

@contextmanager
def staging_directory(parent):
    path = Path(parent) / ('.td-export-' + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        if path.exists():
            shutil.rmtree(path)

def numbers(value):
    values = [float(v) for v in re.split('[,;\\s]+', value.strip()) if v]
    if not values or any((not __import__('math').isfinite(v) for v in values)):
        raise ValueError(f"{tr('无效尺寸或坐标：')}{value}")
    return values

def multiply(value, k):
    return ' '.join((fmt(v * k) for v in numbers(value)))

class ScalePackage:

    def __init__(self, source, destination, factor, game_root='', progress=lambda s: None, cancel=lambda: False, mirror_axis=None, tabs_parameters=True, mass_mode='volume', preserve_nodes=()):
        self.source = Path(source).resolve()
        self.destination = Path(destination).resolve()
        if isinstance(factor, bool) or not isinstance(factor, int) or factor < 1:
            raise ValueError(tr('放大倍数必须是正整数'))
        self.k = factor
        self.game = game_root
        self.progress = progress
        self.cancel = cancel
        self.preserve_nodes = frozenset(preserve_nodes)
        self.mirror_axis = mirror_axis
        self.tabs_parameters = tabs_parameters
        self.mass_mode = mass_mode
        if mass_mode not in {'linear', 'volume'}:
            raise ValueError(tr('质量倍数模式无效'))
        self.tabs_detected = False
        self.script_cache = {}
        self.mod_root = _try_mod_root(self.source.parent)
        self.root = self.mod_root or self.source.parent
        self.report = {'source': str(self.source), 'factor': factor, 'geometry_scale': 1, 'vox': [], 'xml': [], 'scripts': [], 'warnings': [], 'verified': False}
        self.xml_done = set()
        self.active = set()
        self.assets = {}
        self.namespace = self.destination.stem + '_assets_' + uuid.uuid4().hex[:16]
        self.resource_cache = {}
        self.pivot_cache = {}

    def final_path(self, path):
        path = Path(path).resolve()
        return self.destination.parent / path.relative_to(self.stage) if path.is_relative_to(self.stage) else path

    def reference(self, target, destination_xml):
        target = self.final_path(target)
        destination_xml = self.final_path(destination_xml)
        if self.mod_root and target.is_relative_to(self.mod_root):
            return 'MOD/' + target.relative_to(self.mod_root).as_posix()
        return Path(os.path.relpath(target, destination_xml.parent)).as_posix()

    def dependency(self, source, ref, destination_xml):
        resolved = resolve_path(source, ref, self.game)
        if (resolved is None or not resolved.is_file()) and asset_suffix(ref) in {'.lua', '.luau'}:
            builtin = Path(self.game) / 'data' / 'script' / ref
            if builtin.is_file():
                return ref
        if resolved is None or not resolved.is_file():
            raise FileNotFoundError(f"{tr('找不到依赖资源：')}{ref}{tr('\n来自：')}{source}")
        resolved = resolved.resolve()
        if resolved not in self.resource_cache:
            with resolved.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            self.resource_cache[resolved] = digest
            if resolved.suffix.lower() in {'.lua', '.luau'}:
                self.report['scripts'].append({'file': str(resolved), 'sha256': digest, 'mode': 'original_reference'})
        if ref.upper().startswith('BUILT-IN/'):
            return ref
        return self.reference(resolved, destination_xml)

    def check(self):
        if self.cancel():
            raise Cancelled(tr('已取消，原文件未修改'))

    def preflight(self, validate_scale=True):
        visited = set()
        stack = set()
        required = 1
        incompatible = []

        def document(path):
            nonlocal required
            path = Path(path).resolve()
            if path in stack:
                raise ValueError(f"{tr('XML 循环引用：')}{path}")
            if path in visited:
                return
            stack.add(path)
            raw = path.read_bytes()
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError(tr('不支持 DTD/ENTITY'))

            def walk(n, inh):
                nonlocal required
                self.check()
                effective = {**inh, **n.attrib}
                kind = n.tag.rsplit('}', 1)[-1].lower()
                if 'TABS' in effective.get('tags', '').split():
                    self.tabs_detected = True
                if kind in {'vox', 'voxbox', 'box', 'voxagon'}:
                    s = Fraction(effective.get('scale', '1'))
                    if s <= 0:
                        raise ValueError(tr('几何 scale 必须为正数'))
                    required = lcm(required, s.denominator)
                    if (s * self.k).denominator != 1:
                        incompatible.append(f"{path.name} / {kind} {n.get('name', n.get('object', ''))}：scale={s}")
                if validate_scale and kind == 'voxscript':
                    raise ValueError(tr('包含 voxscript 动态生成几何，无法保证静态等比放大；未导出'))
                if kind in {'instance', 'include'} and asset_suffix(effective.get('file', '')) in {'.xml', '.prefab'}:
                    dependency = resolve_path(path, effective['file'], self.game)
                    if dependency is None or not dependency.is_file():
                        raise FileNotFoundError(effective['file'])
                    document(dependency)
                child = dict(inh)
                if kind == 'group':
                    for key, value in sorted(n.attrib.items(), key=lambda kv: int(kv[0][4:]) if re.fullmatch('prop\\d+', kv[0]) else -1):
                        if re.fullmatch('prop\\d+', key) and '=' in value:
                            key, value = value.split('=', 1)
                            child[key.strip().lower()] = value.strip()
                for c in n:
                    walk(c, child)
            walk(ET.fromstring(raw), {})
            stack.remove(path)
            visited.add(path)
        document(self.source)
        self.report['minimum_exact_factor'] = required
        self.report['fractional_scale_inputs'] = incompatible
        self.report['scale_policy'] = 'integer product: replicate voxels; fractional product: preserve voxel resolution and use multiplied XML scale'

    def target_xml(self, path):
        path = Path(path).resolve()
        if path == self.source:
            return self.stage / self.destination.name
        return self.stage / self.namespace / 'xml' / (hashlib.sha256(str(path).encode()).hexdigest()[:12] + '_' + path.name)

    def voxel(self, xml, ref, q, scene, destination_xml, object_name=''):
        object_name = object_name or split_vox_reference(ref)[1]
        src = resolve_path(xml, ref, self.game)
        if src is None or not src.is_file():
            raise FileNotFoundError(f"{tr('找不到 VOX：')}{ref}{tr('\n来自：')}{xml}")
        src = src.resolve()
        key = (src, q, scene, object_name)
        if key not in self.assets:
            self.resource_cache[src] = hashlib.sha256(src.read_bytes()).hexdigest()
            digest = hashlib.sha256((str(src) + str(q) + str(scene) + object_name).encode()).hexdigest()[:16]
            rel = Path(self.namespace) / 'vox' / (digest + '_' + src.name.rstrip(' .'))
            self.progress(tr('放大体素：') + src.name)
            result = bake_scene(src, self.stage / rel, q, object_name, False, self.check)
            result['usage'] = 'shape' if scene else 'brush'
            result.update(source=str(src), output=rel.as_posix())
            self.report['vox'].append(result)
            self.assets[key] = self.stage / rel
        return (self.reference(self.assets[key], destination_xml), src)

    def process(self, source):
        source = Path(source).resolve()
        if source in self.active:
            raise ValueError(f"{tr('XML 循环引用：')}{source}")
        if source in self.xml_done:
            return self.target_xml(source)
        self.active.add(source)
        self.check()
        self.progress(tr('处理 XML：') + source.name)
        destination_xml = self.target_xml(source)
        raw = source.read_bytes()
        self.resource_cache[source] = hashlib.sha256(raw).hexdigest()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError(tr('不支持 DTD/ENTITY'))
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        tree = ET.ElementTree(ET.fromstring(raw, parser=parser))
        root = tree.getroot()
        before = [(n.tag, n.get('name'), len(n)) for n in root.iter()]
        elements = [n for n in root if isinstance(n.tag, str)]
        editor_wrapper = elements[0] if root.tag == 'prefab' and len(elements) == 1 and (elements[0].tag == 'group') and elements[0].get('name', '').lower().startswith('instance=') else None

        def walk(node, inherited, parent_delta=(0, 0, 0)):
            if not isinstance(node.tag, str):
                return
            self.check()
            kind = node.tag.rsplit('}', 1)[-1].lower()
            explicit = dict(node.attrib)
            effective = {**inherited, **explicit}
            k = self.k
            delta = (0, 0, 0)
            geometry = kind in {'vox', 'voxbox', 'box', 'voxagon'}
            q = k
            if geometry:
                value = Fraction(effective.get('scale', '1')) * k
                if value <= 0:
                    raise ValueError(tr('几何 scale 必须为正数'))
                if value.denominator == 1:
                    q = int(value)
                    node.set('scale', '1')
                else:
                    q = 1
                    node.set('scale', fmt(float(value)))
                    self.report['geometry_scale'] = 'mixed: fractional products retained'
                    self.report.setdefault('fractional_scales', []).append({'xml': str(source), 'kind': kind, 'name': effective.get('name', effective.get('object', '')), 'original': effective.get('scale', '1'), 'output': node.get('scale'), 'voxel_replication': 1})
            if kind == 'vox':
                ref = effective.get('file', '')
                if asset_suffix(ref) != '.vox':
                    raise ValueError(f"{tr('不支持的 vox file：')}{ref}")
                name = effective.get('object', '').strip()
                result, src = self.voxel(source, ref, q, True, destination_xml, name)
                node.set('file', result)
                node.set('object', '')
            if kind in {'voxbox', 'box'}:
                node.set('size', ' '.join((str(v * q) for v in box_size(effective.get('size', '50 30 20')))))
            if kind in {'voxbox', 'box', 'voxagon'}:
                brush = effective.get('brush', '')
                if asset_suffix(brush) == '.vox':
                    node.set('brush', self.voxel(source, brush, q, False, destination_xml, effective.get('object', '').strip())[0])
                    node.set('object', '')
                if 'offset' in effective:
                    node.set('offset', multiply(effective['offset'], q))
            if kind == 'voxagon':
                node.set('extrude', multiply(effective.get('extrude', '1'), q))
            if 'pos' in effective:
                p = [v * k for v in numbers(effective['pos'])]
            else:
                p = [0.0, 0.0, 0.0]
            if kind != 'vertex':
                if len(p) > 3:
                    raise ValueError(f"{kind}{tr(' pos 超过三个分量')}")
                p = (p + [0.0, 0.0, 0.0])[:3]
                angles = numbers(effective.get('rot', '0 0 0'))
                r = euler_matrix((angles + [0, 0, 0])[:3])
                p = [p[i] - parent_delta[i] + sum((r[i][j] * delta[j] for j in range(3))) for i in range(3)]
            if node is not editor_wrapper and ('pos' in effective or any(delta) or any(parent_delta)):
                node.set('pos', ' '.join((fmt(v) for v in p)))
            if kind == 'light' and 'scale' in effective:
                node.set('scale', effective['scale'])
            fields = {'wheel': ('radius', 'width', 'travel'), 'trigger': ('size', 'radius'), 'water': ('size', 'depth'), 'rope': ('size', 'length', 'slack', 'radius', 'maxstretch'), 'light': ('size', 'reach', 'unshadowed'), 'location': (), 'screen': ('size',), 'joint': ('size', 'slack', 'maxstretch'), 'boundary': ('height',)}.get(kind, ())
            for field in fields:
                if field in effective:
                    node.set(field, multiply(effective[field], k))
            defaults = {'joint': {'size': '0.1'}, 'rope': {'size': '0.2'}, 'wheel': {'travel': '-0.1 0.1'}, 'screen': {'size': '0.9 0.5'}}.get(kind, {})
            for field, value in defaults.items():
                if field not in effective:
                    node.set(field, multiply(value, k))
            if kind == 'joint' and effective.get('type', '').lower() in {'prismatic', 'slider', 'rope'} and ('limits' in effective):
                node.set('limits', multiply(effective['limits'], k))
            if kind == 'joint' and effective.get('type', '').lower() in {'prismatic', 'slider'} and ('iklimits' in effective):
                node.set('iklimits', multiply(effective['iklimits'], k))
            if kind in {'instance', 'include'} and asset_suffix(effective.get('file', '')) in {'.xml', '.prefab'}:
                dep = resolve_path(source, effective['file'], self.game)
                if dep is None or not dep.is_file():
                    raise FileNotFoundError(f"{tr('找不到引用 XML：')}{effective['file']}")
                dst = self.process(dep)
                node.set('file', self.reference(dst, destination_xml))
            elif kind != 'vox' and 'file' in effective and (asset_suffix(effective['file']) in {'.lua', '.luau', '.png', '.jpg', '.ogg', '.wav', '.dds'}):
                if kind == 'script' and self.tabs_detected and self.tabs_parameters:
                    from tabs_scaling import script_reference
                    node.set('file', script_reference(self, source, effective['file'], destination_xml))
                else:
                    node.set('file', self.dependency(source, effective['file'], destination_xml))
            if kind == 'screen' and asset_suffix(effective.get('script', '')) in {'.lua', '.luau'}:
                node.set('script', self.dependency(source, effective['script'], destination_xml))
            if kind == 'voxscript':
                raise ValueError(tr('包含运行时生成几何的 voxscript，无法在保留脚本原文的同时保证等比放大，已停止导出'))
            child_inherited = dict(inherited)
            if self.tabs_detected:
                from tabs_scaling import scale_tags
                if 'tags' in explicit:
                    node.set('tags', scale_tags(explicit['tags'], kind, k, self.tabs_parameters, self.report))
            if kind == 'group':
                for key, value in sorted(explicit.items(), key=lambda kv: int(kv[0][4:]) if re.fullmatch('prop\\d+', kv[0]) else -1):
                    if re.fullmatch('prop\\d+', key, re.I) and '=' in value:
                        attr, val = value.split('=', 1)
                        attr = attr.strip().lower()
                        val = val.strip()
                        child_inherited[attr] = val
                        if attr == 'scale':
                            node.set(key, attr + '=1')
                        if attr == 'tags' and self.tabs_detected:
                            from tabs_scaling import scale_tags
                            node.set(key, attr + '=' + scale_tags(val, 'group', k, self.tabs_parameters, self.report))
            if self.tabs_detected and 'tags' not in explicit and ('tags' in effective):
                from tabs_scaling import scale_tags
                updated = scale_tags(effective['tags'], kind, k, self.tabs_parameters, self.report)
                if updated != effective['tags']:
                    node.set('tags', updated)
            for child in node:
                walk(child, child_inherited, delta)
        walk(root, {})
        after = [(n.tag, n.get('name'), len(n)) for n in root.iter()]
        if before != after:
            raise ValueError(tr('XML 层级、name 或 tags 校验失败'))
        target = self.target_xml(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        tree.write(target, encoding='utf-8', xml_declaration=True)
        self.xml_done.add(source)
        self.active.remove(source)
        self.report['xml'].append(str(target.relative_to(self.stage)))
        return target

    def build(self):
        if not self.source.is_file():
            raise FileNotFoundError(self.source)
        if self.destination.suffix.lower() not in {'.xml', '.prefab'}:
            raise ValueError(tr('输出必须是 XML 文件名'))
        if self.destination.parent != self.source.parent:
            raise ValueError(tr('请在原 XML 所在文件夹内输出'))
        if self.destination == self.source:
            raise ValueError(tr('不能覆盖原 XML，请使用新文件名'))
        report_path = self.destination.with_suffix('.report.json')
        final_assets = self.destination.parent / self.namespace
        for path in (self.destination, report_path, final_assets):
            if path.exists():
                raise FileExistsError(tr('输出已存在，请使用新文件名：') + str(path))
        self.progress(tr('检查 XML 层级、原 scale 和引用…'))
        self.preflight(validate_scale=not bool(self.mirror_axis))
        original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with staging_directory(self.destination.parent) as temporary:
            self.stage = Path(temporary).resolve()
            if self.mirror_axis:
                from mirror_package import axis_reflection
                self.check()
                self.progress(tr('处理镜像与层级旋转…'))
                builder = SiblingMirror(self)
                target = self.target_xml(self.source)
                builder._process_xml(self.source, target, axis_reflection(self.mirror_axis), top=True)
                builder._verify_tags(self.source, target)
                self.report['mirror'] = builder.report.as_dict()
                self.report['mirror']['preserve_nodes'] = sorted(self.preserve_nodes)
                self.report['mirror']['preserve_policy'] = "reflect subtree anchor; preserve world orientation, geometry and internal transforms"
                self.report['geometry_scale'] = tr('镜像保留原几何 scale')
            else:
                target = self.process(self.source)
            for path in self.assets.values():
                if not path.is_relative_to(self.stage / self.namespace) or not path.is_file():
                    raise ValueError(tr('新旧 VOX 引用隔离校验失败'))
            if hashlib.sha256(self.source.read_bytes()).hexdigest() != original_hash:
                raise ValueError(tr('处理期间原 XML 已改变，已取消导出'))
            for path, digest in self.resource_cache.items():
                with path.open('rb') as stream:
                    current = hashlib.file_digest(stream, 'sha256').hexdigest()
                if current != digest:
                    raise ValueError(tr('处理期间引用资源已改变：') + str(path))
            self.report['asset_namespace'] = self.namespace
            self.report['output_xml'] = str(self.destination)
            self.report['output_directory'] = str(self.destination.parent)
            self.report['report_file'] = str(report_path)
            self.report['export_scope'] = 'selected_xml_and_transformed_references'
            self.report['source_hashes'] = {str(path): digest for path, digest in self.resource_cache.items()}
            if 'mirror' in self.report:

                def final_paths(value):
                    if isinstance(value, str) and value.startswith(str(self.stage) + os.sep):
                        return str(self.destination.parent) + value[len(str(self.stage)):]
                    if isinstance(value, list):
                        return [final_paths(v) for v in value]
                    if isinstance(value, dict):
                        return {k: final_paths(v) for k, v in value.items()}
                    return value
                self.report['mirror'] = final_paths(self.report['mirror'])
            self.report['warnings'].append(tr('原脚本未修改；检测到 TABS 且启用参数调整时，会生成车辆脚本及其 include 副本。其他脚本仍引用原文件。'))
            self.report['warnings'].append(tr('TABS 全局框架中的弹道孔径上限、速度和其他运行时常量不由车辆副本控制，游戏行为仍需验证。'))
            self.report['warnings'].append(tr('灯光 scale 原样保留。') + (tr('镜像保留原几何 scale。') if self.mirror_axis else tr('整数 scale 乘积通过复制体素实现；小数乘积写入 XML scale，不重复复制体素。')) + tr('碰撞与破坏仍需实际游戏验证。'))
            self.report['verified'] = True
            staged_report = self.stage / report_path.name
            staged_report.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
            pairs = []
            if (self.stage / self.namespace).exists():
                pairs.append((self.stage / self.namespace, final_assets))
            pairs.extend([(staged_report, report_path), (target, self.destination)])
            installed = []
            try:
                self.check()
                for staged, final in pairs:
                    if final.exists():
                        raise FileExistsError(tr('输出已存在：') + str(final))
                    staged.rename(final)
                    installed.append(final)
            except BaseException:
                for final in reversed(installed):
                    if final.is_dir():
                        shutil.rmtree(final)
                    else:
                        final.unlink()
                raise
        return self.report
from mirror_package import MirrorPackageBuilder

class SiblingMirror(MirrorPackageBuilder):
    """Reuse mirror geometry while sharing sibling-export dependency handling."""

    def __init__(self, owner):
        super().__init__(owner.source, owner.destination, owner.mirror_axis, owner.game)
        self.owner = owner
        self._frames = []
        self._node_paths = {}
        self._stage = owner.stage
        self.assets = owner.stage / owner.namespace

    def _named_vox_pivot_correction(self, source_xml, effective):
        return 0.0

    def _prepare_document(self, root, source):
        from selection import indexed_nodes
        self._node_paths = {id(n): path for path, n, _ in indexed_nodes(root)}
        if Path(source).resolve() == self.owner.source:
            invalid = self.owner.preserve_nodes - set(self._node_paths.values())
            if root.tag.rsplit('}', 1)[-1].lower() in {'scene', 'prefab'} and '0' in self.owner.preserve_nodes:
                invalid = invalid | {'0'}
            if invalid:
                from localization import choose
                raise ValueError(choose('Selected parts changed; reload the parts tree.', '所选部件已改变，请重新加载部件树。'))

    def _process_xml(self, source, *args, **kwargs):
        path = Path(source).resolve()
        self.owner.resource_cache[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = self._frames, self._node_paths
        self._frames, self._node_paths = [], {}
        try:
            return super()._process_xml(source, *args, **kwargs)
        finally:
            self._frames, self._node_paths = previous

    def _verify_tags(self, source, output):
        from selection import indexed_nodes
        from tabs_scaling import scale_tags
        from mirror_package import local_name
        a = list(indexed_nodes(ET.parse(source).getroot()))
        b = list(indexed_nodes(ET.parse(output).getroot()))
        # Vertices can be reordered, but do not carry TABS entity tags.
        a = [(p,n) for p,n,_ in a if local_name(n.tag) != 'vertex']
        b = [(p,n) for p,n,_ in b if local_name(n.tag) != 'vertex']
        if len(a) != len(b):
            raise ValueError(tr('镜像标签保留检查失败'))
        selected = self.owner.preserve_nodes if Path(source).resolve() == self.owner.source else ()
        for (path, old), (_, new) in zip(a,b):
            tags = old.get('tags')
            preserved = any(path == p or path.startswith(p + '/') for p in selected)
            if tags and self.owner.tabs_detected and not preserved:
                tags = scale_tags(tags, local_name(old.tag), 1, False, {}, mirror=True)
            if old.tag != new.tag or tags != new.get('tags'):
                raise ValueError(tr('镜像标签保留检查失败'))

    def _preserve_resources(self, node, source, destination, inherited):
        from selection import child_properties
        from mirror_package import split_vox_reference
        self.owner.check()
        if not isinstance(node.tag, str):
            return
        effective = {**inherited, **node.attrib}
        for attr in ('file', 'brush', 'script'):
            ref = effective.get(attr, '')
            if asset_suffix(ref) in {'.vox', '.xml', '.prefab', '.lua', '.luau', '.png', '.jpg', '.jpeg', '.ogg', '.wav', '.dds'}:
                _, object_name = split_vox_reference(ref)
                rewritten = self.owner.dependency(source, ref, destination)
                node.set(attr, rewritten)
                if attr in ('file', 'brush') and object_name and not effective.get('object'):
                    node.set('object', object_name)
        inherited = child_properties(node, inherited)
        for child in node:
            self._preserve_resources(child, source, destination, inherited)

    def _mirror_vox_ref(self, source_xml, value, destination_xml, scene_pivot=False):
        kind, name = self._current_geometry
        name = name or split_vox_reference(value)[1]
        src = resolve_path(source_xml, value, self.game_root)
        if src is None or not src.is_file():
            raise FileNotFoundError(value)
        key = (src.resolve(), name)
        if key not in self.vox_cache:
            self.owner.resource_cache[src.resolve()] = hashlib.sha256(src.read_bytes()).hexdigest()
            digest = hashlib.sha256((str(src) + name).encode()).hexdigest()[:16]
            target = self.assets / 'vox' / (digest + '_' + src.name.rstrip(' .'))
            self.owner.progress(tr('镜像体素：') + src.name)
            result = bake_scene(src, target, 1, name, True, self.owner.check)
            result.update(source=str(src), output=target.relative_to(self._stage).as_posix(), usage='shape' if kind == 'vox' else 'brush')
            self.owner.report['vox'].append(result)
            self.vox_cache[key] = target
            self.report.vox_files.append(str(target))
        return self._virtual_ref(self.vox_cache[key], destination_xml)

    def _walk(self, element, source_xml, destination_xml, root_reflection, parent_correction, inherited, root_node=False, suppress_transform=False):
        self.owner.check()
        if not isinstance(element.tag, str):
            return
        from mirror_package import identity, local_matrix, parse_vector, matmul, rigid_inverse, vec_text, matrix_euler
        effective = {**inherited, **element.attrib}
        original_local = identity() if suppress_transform else local_matrix(parse_vector(effective.get('pos')), parse_vector(effective.get('rot')))
        if self._frames:
            parent_element, parent_original, grandparent_output, parent_suppressed = self._frames[-1]
            parent_local = identity() if parent_suppressed else local_matrix(parse_vector(parent_element.get('pos')), parse_vector(parent_element.get('rot')))
            parent_output = matmul(grandparent_output, parent_local)
        else:
            parent_original = parent_output = identity()
        original_world = matmul(parent_original, original_local)
        path = self._node_paths.get(id(element), '')
        preserved = source_xml.resolve() == self.owner.source and path in self.owner.preserve_nodes
        if preserved and not suppress_transform:
            desired = [list(row) for row in original_world]
            reflected = matmul(root_reflection, original_world)
            for i in range(3):
                desired[i][3] = reflected[i][3]
            output = matmul(rigid_inverse(parent_output), desired)
            element.set('pos', vec_text(row[3] for row in output[:3]))
            element.set('rot', vec_text(matrix_euler(output)))
            self._preserve_resources(element, source_xml, destination_xml, inherited)
            return
        if preserved and suppress_transform:
            # The editor's instance wrapper has no transform. Preserve each
            # promoted child as an independent subtree anchor.
            self.owner.preserve_nodes = self.owner.preserve_nodes | {self._node_paths[id(c)] for c in element if isinstance(c.tag,str)}
        kind = element.tag.rsplit('}', 1)[-1].lower()
        self._current_geometry = (kind, effective.get('object', '').strip())
        self._frames.append((element, original_world, parent_output, suppress_transform))
        try:
            super()._walk(element, source_xml, destination_xml, root_reflection, parent_correction, inherited, root_node, suppress_transform)
        finally:
            self._frames.pop()
        if kind == 'vox' or (kind in {'voxbox', 'box', 'voxagon'} and asset_suffix(effective.get('brush', '')) == '.vox'):
            element.set('object', '')
        if self.owner.tabs_detected and element.get('tags') and not preserved:
            from tabs_scaling import scale_tags
            element.set('tags', scale_tags(element.get('tags'), kind, 1, False, self.owner.report, mirror=True))
        ref = effective.get('file', '')
        if kind == 'include' and asset_suffix(ref) in {'.xml', '.prefab'}:
            element.set('file', self._instance_ref(source_xml, ref, destination_xml))
        elif kind not in {'vox', 'instance'} and asset_suffix(ref) in {'.lua', '.luau', '.png', '.jpg', '.ogg', '.wav', '.dds'}:
            element.set('file', self.owner.dependency(source_xml, ref, destination_xml))
        if kind == 'screen' and asset_suffix(effective.get('script', '')) in {'.lua', '.luau'}:
            element.set('script', self.owner.dependency(source_xml, effective['script'], destination_xml))
