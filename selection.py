"""Stable XML selections shared by the GUI and unattended API."""
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

def child_properties(node, inherited):
    result = dict(inherited)
    if isinstance(node.tag,str) and node.tag.rsplit('}',1)[-1].lower() == 'group':
        for key,value in sorted(node.attrib.items(), key=lambda kv: int(kv[0][4:]) if re.fullmatch(r'prop\d+',kv[0]) else -1):
            if re.fullmatch(r'prop\d+',key) and '=' in value:
                k,v = value.split('=',1)
                result[k.strip().lower()] = v.strip()
    return result

def indexed_nodes(root):
    def walk(node,path,parent):
        if not isinstance(node.tag,str): return
        yield path,node,parent
        # Ignore comments/PIs so paths are identical in both XML parsers.
        for i,child in enumerate(c for c in node if isinstance(c.tag,str)):
            yield from walk(child,path+'/'+str(i),path)
    yield from walk(root,'0','')

def inspect_parts(source):
    raw=Path(source).read_bytes()
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DTD/entities are not supported')
    root=ET.fromstring(raw)
    rows=[]
    for path,node,parent in indexed_nodes(root):
        kind=node.tag.rsplit('}',1)[-1].lower()
        if kind == 'vertex':continue
        rows.append(dict(path=path,parent=parent,kind=kind,name=node.get('name',node.get('object','')),
                         pos=node.get('pos','0 0 0'),rot=node.get('rot','0 0 0'),tags=node.get('tags',''),
                         selectable=kind not in {'scene','prefab'}))
    return {'sha256':hashlib.sha256(raw).hexdigest(),'parts':rows}
