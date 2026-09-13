"""Per-export TABS adjustments. Never write a shared script or preset."""
from localization import tr
from pathlib import Path
import hashlib, json, re
from mirror_package import resolve_path, fmt
LENGTH_FIELDS = {'Caliber', 'Projectile_Caliber', 'Shell_Caliber', 'Momentum', 'Penetration', 'Recoil_Distance', 'Max_Range', 'Proximity_Distance', 'Fuze_Distance', 'Arming_Distance', 'Minimum_Arming_Distance', 'Standoff', 'Standoff_Distance', 'Canister_Caliber', 'Canister_Momentum', 'Smoke_Radius'}
MASS_FIELDS = {'Mass', 'Explosive_Payload'}
ARMOR_TAGS = {'RHA', 'CA', 'ERA'}

def scale_tags(text, kind, k, parameters, report, mirror=False):
    tokens = re.findall('\\S+', text)
    values = dict((t.split('=', 1) if '=' in t else (t, '') for t in tokens))
    changed = {}

    def length(key, mult):
        if key in values:
            try:
                changed[key] = fmt(float(values[key]) * mult)
            except ValueError:
                raise ValueError(f"{tr('TABS 标签 ')}{key}{tr(' 不是数值：')}{values[key]}")
    if not mirror:
        for key in ('Spacing', 'Travel_Up', 'Travel_Down', 'trackdist'):
            length(key, k)
        if kind == 'body' and values.get('Part') == 'Hull':
            for key, default in {'Spacing': 0.31, 'Travel_Up': 0.3, 'Travel_Down': 0.1}.items():
                if key not in values:
                    changed[key] = fmt(default * k)
        if parameters:
            for key in ARMOR_TAGS:
                length(key, k)
    if kind == 'body' and 'unassignedtrackpiece' in values:
        d = float(values.get('trackdist') or 3)
        changed['trackdist'] = fmt(d + 4 if mirror else d * k + 2 * (k - 1))
    out = []
    for token in tokens:
        key = token.split('=', 1)[0]
        out.append(key + '=' + changed[key] if key in changed else token)
    out.extend((key + '=' + value for key, value in changed.items() if key not in values))
    result = ' '.join(out)
    if changed and result != text:
        report.setdefault('tabs_tags', []).append({'kind': kind, 'before': text, 'after': result})
    return result if changed else text
TOKEN = re.compile('(?:--\\[(=*)\\[.*?\\]\\1\\]|--[^\\r\\n]*|\\s+)|(?:"(?:\\\\.|[^"\\\\])*"|\'(?:\\\\.|[^\'\\\\])*\')|(?:\\[(=*)\\[.*?\\]\\2\\])|(?:[A-Za-z_]\\w*|(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?|.)', re.S)

def tokens(text):
    return [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(text) if not m.group().isspace() and (not m.group().startswith('--'))]

def table_spans(text):
    ts = tokens(text)
    stack = []
    spans = {}
    for i, (v, a, b) in enumerate(ts):
        if v == '{':
            stack.append(i)
        elif v == '}':
            if not stack:
                raise ValueError(tr('Lua 表括号不匹配'))
            spans[stack.pop()] = i
    if stack:
        raise ValueError(tr('Lua 表括号未闭合'))
    return (ts, spans)

def string_value(v):
    if v[:1] in ('"', "'"):
        return v[1:-1]
    return v

def shell_defaults(owner, script_text):
    ts, sp = table_spans(script_text)
    wanted = set()
    for i, end in sp.items():
        if i >= 4 and [ts[j][0] for j in (i - 4, i - 2, i - 1)] == ['[', ']', '=']:
            j = i + 1
            while j < end:
                if j + 2 < end and [ts[n][0] for n in range(j, j + 3)] == ['Custom', '=', 'false']:
                    wanted.add(string_value(ts[i - 3][0]))
                    break
                j = sp[j] + 1 if j in sp else j + 1
    if not wanted:
        return ({}, None)
    candidates = [owner.root / 'TABS/scripts/ammo.lua', Path(owner.game).parent.parent / 'workshop/content/1167630/3148138736/TABS/scripts/ammo.lua']
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        raise FileNotFoundError(tr('需要读取 TABS 的 TABS/scripts/ammo.lua，才能放大内置弹药预设。请填写正确的游戏目录。'))
    raw = source.read_bytes()
    owner.resource_cache[source.resolve()] = hashlib.sha256(raw).hexdigest()
    text = raw.decode('utf-8-sig')
    ts, sp = table_spans(text)
    start = next((i + 2 for i in range(len(ts) - 2) if [ts[j][0] for j in range(i, i + 3)] == ['shellTypes', '=', '{']), None)
    if start is None:
        raise ValueError(tr('TABS shellTypes 格式无法识别，已停止避免漏改弹药'))
    presets = {}
    i = start + 1
    while i < sp[start]:
        if ts[i][0] == '[' and i + 4 < len(ts) and (ts[i + 2][0] == ']') and (ts[i + 3][0] == '=') and (ts[i + 4][0] == '{'):
            op = i + 4
            end = sp[op]
            presets[string_value(ts[i + 1][0])] = text[ts[op][1]:ts[end][2]]
            i = end + 1
        else:
            i += 1
    selected = {}
    pending = list(wanted)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in presets:
            raise ValueError(tr('TABS 弹药预设不存在，未导出：') + name)
        block = presets[name]
        bt, bs = table_spans(block)
        for j, (v, a, b) in enumerate(bt):
            if re.fullmatch('[A-Za-z_]\\w*', v) and v not in {'true', 'false', 'nil'} and (not (j + 1 < len(bt) and bt[j + 1][0] == '=')):
                raise ValueError(tr('弹药预设包含无法静态复制的表达式：') + name + ' / ' + v)
        selected[name] = block
        for match in re.finditer('\\["([^"\\n]+)"\\]\\s*=\\s*\\{\\s*Custom\\s*=\\s*false', block):
            pending.append(match[1])
    owner.report.setdefault('tabs_preset_sources', []).append({'source': str(source), 'sha256': hashlib.sha256(raw).hexdigest(), 'presets': sorted(selected)})
    return (selected, source)

def lua_adapter(k, mass_mode, presets):
    defaults = '{' + ','.join(('[' + json.dumps(name) + ']=' + value for name, value in presets.items())) + '}'
    lengths = '{' + ','.join(('[' + json.dumps(key) + ']=true' for key in sorted(LENGTH_FIELDS))) + '}'
    masses = '{' + ','.join(('[' + json.dumps(key) + ']=true' for key in sorted(MASS_FIELDS))) + '}'
    return f"""\n-- Generated per-vehicle enlargement adapter. Original files are unchanged.\ndo\n    local factor = {k}\n    local massFactor = {(k ** 3 if mass_mode == 'volume' else k)}\n    local defaults = {defaults}\n    local lengths = {lengths}\n    local masses = {masses}\n    local seen = setmetatable({{}}, {{__mode="k"}})\n    local function copy(t)\n        if type(t) ~= "table" then return t end\n        local r = {{}}; for a,b in pairs(t) do r[a] = copy(b) end; return r\n    end\n    local function expand(t)\n        for key,value in pairs(t or {{}}) do\n            if type(value) == "table" then\n                if (key == "Shell_Presets" or key == "Ammo_Types") then\n                    for id,preset in pairs(value) do\n                        if type(preset) == "table" and preset.Custom ~= true and defaults[id] then\n                            local merged = copy(defaults[id])\n                            for field,setting in pairs(preset) do merged[field] = setting end\n                            merged.Custom = true; value[id] = merged\n                        end\n                    end\n                end\n                expand(value)\n            end\n        end\n    end\n    local function scale(t)\n        if type(t) ~= "table" or seen[t] then return end\n        seen[t] = true\n        for key,value in pairs(t) do\n            if type(value) == "table" then scale(value)\n            elseif type(value) == "number" then\n                if lengths[key] then t[key] = value*factor\n                elseif masses[key] then t[key] = value*massFactor end\n            end\n        end\n    end\n    local original = VehicleInit\n    function VehicleInit(v,vehicle,weapons,optics)\n        expand(weapons); scale(vehicle); scale(weapons)\n        return original(v,vehicle,weapons,optics)\n    end\nend\n"""

def script_reference(owner, source_xml, ref, destination_xml):
    source = resolve_path(source_xml, ref, owner.game)
    if source is None or not source.is_file():
        return owner.dependency(source_xml, ref, destination_xml)
    text = source.read_text(encoding='utf-8-sig')
    if not re.search('\\bVehicleInit\\s*\\(', text) or not re.search('\\bWeapons\\s*=', text):
        return owner.dependency(source_xml, ref, destination_xml)
    if source.resolve() in owner.script_cache:
        return owner.reference(owner.script_cache[source.resolve()], destination_xml)
    presets, preset_source = shell_defaults(owner, text)
    cache = {}
    active = set()

    def clone(path):
        path = path.resolve()
        owner.check()
        if path in active:
            raise ValueError(tr('Lua include 循环引用：') + str(path))
        if path in cache:
            return cache[path]
        raw = path.read_bytes()
        owner.resource_cache[path] = hashlib.sha256(raw).hexdigest()
        code = raw.decode('utf-8-sig')
        active.add(path)
        safe_name = re.sub('[^A-Za-z0-9_.-]', '_', path.name)
        target = owner.stage / owner.namespace / 'scripts' / (hashlib.sha256(str(path).encode()).hexdigest()[:12] + '_' + safe_name)

        def include(m):
            value = m[2]
            dep = resolve_path(source_xml, value, owner.game) if re.match('^(MOD|LEVEL|BUILT-IN)/', value, re.I) else path.parent / value
            if dep is None or not dep.is_file():
                raise FileNotFoundError(tr('Lua include 不存在：') + str(value))
            copied = clone(dep)
            return m[1] + './' + copied.name + m[3]
        code = re.sub('(?m)^(\\s*#include\\s*["\\\'])([^"\\\'\\r\\n]+)(["\\\'])', include, code)
        code = re.sub('(["\\\'])LEVEL/([^"\\\'\\r\\n]+)(["\\\'])', lambda m: m[1] + owner.reference(source_xml.parent / source_xml.stem / m[2], target) + m[3], code)
        if path == source.resolve():
            code += lua_adapter(owner.k, owner.mass_mode, presets)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding='utf-8')
        cache[path] = target
        active.remove(path)
        owner.report['scripts'].append({'file': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'output': str(target.relative_to(owner.stage)), 'mode': 'isolated_copy'})
        return target
    target = clone(source)
    owner.script_cache[source.resolve()] = target
    owner.report['tabs_parameters'] = {'factor': owner.k, 'mass_factor': owner.k ** 3 if owner.mass_mode == 'volume' else owner.k, 'length_fields': sorted(LENGTH_FIELDS), 'mass_fields': sorted(MASS_FIELDS), 'armor_tags': sorted(ARMOR_TAGS), 'CHA': 'unchanged: armor per voxel; voxel thickness already grows'}
    return owner.reference(target, destination_xml)
