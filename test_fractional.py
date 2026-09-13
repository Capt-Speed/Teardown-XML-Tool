import unittest,tempfile,pathlib,hashlib
from collections import Counter
import xml.etree.ElementTree as ET
from scale_package import ScalePackage
from self_test import fixture
from engine_reference import signature
import audit_latest as world

class FractionalTests(unittest.TestCase):
    def test_mixed_fractional_world_geometry_tags_and_children(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);source=fixture(p/'mod');root=source.parent
            source.write_text('''<prefab><group pos="1 2 3" rot="13 27 91" prop0="scale=11/10"><body><wheel pos="1 .2 -.3" tags="l_wheel1 custom=1.1"><vox file="MOD/part.vox" object="part" tags="wheel custom=keep" pos=".1 0 -.2"><joint pos=".01 -.03 .1"/></vox></wheel><voxbox scale="0.2" size="3 5 7" pos="1 2 3" tags="box keep=all"/><vox file="MOD/part.vox" object="part" scale="0.5" tags="integer_product"/><vox scale="1" file="MOD/part.vox" object="part" tags="normal"/></body></group></prefab>''')
            # The exporter accepts exact fractions and decimal notation. The
            # independent XML oracle expects decimal XML numbers.
            original=source.read_bytes();source.write_bytes(original.replace(b'11/10',b'1.1'))
            before=world.scene(source);source.write_bytes(original)
            tags=[n.get('tags') for n in ET.parse(source).iter()]
            report=ScalePackage(source,root/'out.xml',2).build();tree=ET.parse(root/'out.xml')
            self.assertEqual(tags,[n.get('tags') for n in tree.iter()])
            self.assertEqual(tree.find('.//wheel/vox').get('scale'),'2.2')
            self.assertEqual(tree.find('.//voxbox').get('scale'),'0.4')
            self.assertEqual(tree.find('.//voxbox').get('size'),'3 5 7')
            self.assertEqual(tree.find('.//vox[@tags="integer_product"]').get('scale'),'1')
            # Collapse integer replicated blocks to their centroid before
            # comparing world positions against the original cell centres.
            assets={str((root/a['output']).resolve()):a['factor'] for a in report['vox']}
            old=world.vox_points
            def points(path,a):
                q=assets.get(str(path.resolve()),1);cells,_=signature(path,a.get('object',''),q)
                s=.1*float(a.get('scale',1))*q
                return [(((x+.5)*s,(z+.5)*s,-(y+.5)*s),c) for (x,y,z,c),n in cells.items() for _ in range(n//q**3)]
            world.vox_points=points
            try:after=world.scene(root/'out.xml')
            finally:world.vox_points=old
            self.assertEqual(before.keys(),after.keys())
            for key,pts in before.items():
                remaining=after[key][:];self.assertEqual(len(pts),len(remaining))
                for p,c in pts:
                    error,j=min((max(abs(2*p[i]-v[i]) for i in range(3)),j) for j,(v,color) in enumerate(remaining) if color==c)
                    remaining.pop(j);self.assertLess(error,1e-8,key)
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(report['fractional_scales'][0]['output'],'2.2')
            from verify_export import verify_export
            self.assertTrue(verify_export(root/'out.report.json')['ok'])

    def test_nonpositive_and_nonfinite_remain_invalid(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)
            for value in ('0','-0.1','nan','inf'):
                src=p/'in.xml';src.write_text(f'<prefab><voxbox scale="{value}"/></prefab>')
                with self.assertRaises(ValueError):ScalePackage(src,p/'out.xml',2).build()
                self.assertFalse((p/'out.xml').exists())

if __name__=='__main__':unittest.main(verbosity=2)
