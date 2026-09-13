import tempfile
import unittest
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
from self_test import fixture
from scale_package import ScalePackage
from selection import indexed_nodes
import audit_latest as oracle

class SelectionTests(unittest.TestCase):
    def test_rotated_parents_corner_pivots_and_subtrees_all_axes(self):
        for axis in 'XYZ':
            with self.subTest(axis=axis), tempfile.TemporaryDirectory() as t:
                root=Path(t)/'mod';src=fixture(root)
                src.write_text('''<prefab><group pos="1 2 3" rot="21 43 17" prop0="scale=1.1"><body pos=".17 -.29 .31" rot="13 -27 9"><voxbox size="7 9 11" pos=".4 .2 -.1" rot="11 8 31" tags="parent"><vox file="MOD/part.vox" object="part" pos=".13 .27 -.11" rot="18 -33 77" tags="kept"><joint pos=".01 -.02 .03" rot="3 7 11"/></vox><wheel pos=".07 -.31 .09" rot="12 26 2"/></voxbox><voxbox size="3 5 7" pos="-.3 .7 .5" rot="27 81 -53" tags="untouched"/></body></group></prefab>''')
                before=oracle.scene(src)
                tree=ET.parse(src).getroot();parts=list(indexed_nodes(tree));selected=next(p for p,n,_ in parts if n.get('tags')=='kept')
                def transforms(node,parent):
                    world=oracle.mul(parent,oracle.local(node.attrib))
                    if node.get('tags')=='kept':return world
                    for c in node:
                        result=transforms(c,world)
                        if result is not None:return result
                w=transforms(tree,oracle.identity());anchor=tuple(w[i][3] for i in range(3));ai='XYZ'.index(axis)
                report=ScalePackage(src,root/'out.xml',1,mirror_axis=axis,preserve_nodes=[selected]).build()
                after=oracle.scene(root/'out.xml')
                for key,points in before.items():
                    remaining=after[key][:]
                    for point,color in points:
                        p=list(point)
                        if key.endswith(':vox') or ':vox/' in key:p[ai]-=2*anchor[ai]
                        else:p[ai]*=-1
                        distance,j=min((max(abs(p[i]-q[i]) for i in range(3)),j) for j,(q,c) in enumerate(remaining) if c==color)
                        remaining.pop(j);self.assertLess(distance,1e-8,key)
                out=ET.parse(root/'out.xml');kept=out.find('.//vox')
                self.assertEqual(kept.get('file'),'MOD/part.vox');self.assertEqual(kept.get('object'),'part');self.assertEqual(kept.get('tags'),'kept')
                self.assertEqual(kept.find('joint').attrib,tree.find('.//joint').attrib)
                self.assertEqual(report['mirror']['preserve_nodes'],[selected])

    def test_preserved_instance_and_level_resources(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'mod';src=fixture(root);level=root/'model';level.mkdir();(level/'a.lua').write_text('-- unchanged')
            nested=root/'nested.xml';nested.write_text('<prefab><vox file="MOD/part.vox" object="part"/></prefab>')
            src.write_text('<prefab><!-- comment --><body rot="7 19 31"><instance file="MOD/nested.xml" pos="1 2 3" tags="keep"/><script pos="2 3 4" file="LEVEL/a.lua" tags="keep_script"/></body></prefab>')
            originals={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            chosen=[p for p,n,_ in indexed_nodes(ET.parse(src).getroot()) if n.get('tags')]
            report=ScalePackage(src,root/'out.xml',1,mirror_axis='X',preserve_nodes=chosen).build()
            out=ET.parse(root/'out.xml')
            self.assertEqual(out.find('.//instance').get('file'),'MOD/nested.xml')
            self.assertEqual(out.find('.//script').get('file'),'MOD/model/a.lua')
            self.assertEqual(report['vox'],[])
            self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in originals.items()))

    def test_invalid_selection_rolls_back(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'mod';src=fixture(root)
            with self.assertRaises(ValueError):ScalePackage(src,root/'out.xml',1,mirror_axis='X',preserve_nodes=['0/999']).build()
            self.assertFalse((root/'out.xml').exists());self.assertFalse(any(root.glob('.td-export-*')))

    def test_editor_wrapper_and_tabs_tags_remain_intact(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'mod';src=fixture(root)
            src.write_text('<prefab><group name="instance=MOD/model.xml" pos="17 2 8" rot="0 31 0" tags="TABS l_wheel1"><body pos="1 2 3" rot="11 29 3" tags="l_wheel1"><voxbox size="3 5 7" tags="l_wheel1"/></body></group></prefab>')
            out=root/'out.xml';ScalePackage(src,out,1,mirror_axis='Z',preserve_nodes=['0/0']).build()
            before,after=ET.parse(src),ET.parse(out)
            self.assertEqual([n.get('tags') for n in before.iter()],[n.get('tags') for n in after.iter()])
            self.assertEqual(after.find('.//body').get('pos'),'1 2 -3')
            self.assertEqual(after.find('.//body').get('rot'),'11 29 3')
