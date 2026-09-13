import tempfile,unittest,json,hashlib,xml.etree.ElementTree as ET
from pathlib import Path
from self_test import fixture
from scale_package import ScalePackage
from mirror_package import resolve_path

class ReferenceTests(unittest.TestCase):
    def test_shared_level_nested_and_unrelated(self):
        for mode in (None,'X'):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t)/'mod';source=fixture(root)
                level=root/'model';level.mkdir();(level/'local.lua').write_text('-- original LEVEL script',encoding='utf-8')
                child=root/'nested.xml';child.write_text('<prefab><script file="LEVEL/local.lua"><vox file="MOD/part.vox" object="part"/></script></prefab>',encoding='utf-8')
                child_level=root/'nested';child_level.mkdir();(child_level/'local.lua').write_text('-- nested original',encoding='utf-8')
                source.write_text('<prefab><group name="instance=MOD/model.xml" pos="17 3 -9" rot="0 45 0"><script file="LEVEL/local.lua"><instance file="MOD/nested.xml" pos="2 3 4"/><vox file="MOD/part.vox" object="part"/></script></group></prefab>',encoding='utf-8')
                originals={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
                out=root/'changed.xml';report=ScalePackage(source,out,2,mirror_axis=mode).build()
                generated=set(p for p in root.rglob('*') if p.is_file())-set(originals)
                self.assertTrue(all(p==out or p==out.with_suffix('.report.json') or p.is_relative_to(root/report['asset_namespace']) for p in generated))
                self.assertTrue(all(p.suffix in {'.xml','.json','.vox'} for p in generated))
                self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in originals.items()))
                tree=ET.parse(out);script=tree.find('.//script')
                self.assertEqual(resolve_path(out,script.get('file'),''),level/'local.lua')
                instance=tree.find('.//instance');nested=resolve_path(out,instance.get('file'),'')
                self.assertNotEqual(nested,child);self.assertTrue(nested.exists())
                nested_script=ET.parse(nested).find('.//script')
                self.assertEqual(resolve_path(nested,nested_script.get('file'),''),child_level/'local.lua')
                if mode is None:self.assertEqual(tree.find('group').get('pos'),'17 3 -9')

    def test_standalone_no_mod_created(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);source=p/'source.xml';source.write_text('<prefab><voxbox size="3 5 7"/></prefab>')
            out=p/'source_x2.xml';ScalePackage(source,out,2).build()
            self.assertEqual({f.name for f in p.iterdir()},{'source.xml','source_x2.xml','source_x2.report.json'})
            with self.assertRaises(ValueError):ScalePackage(source,source,2).build()
            with self.assertRaises(ValueError):ScalePackage(source,p/'other'/'file.xml',2).build()

if __name__=='__main__':unittest.main(verbosity=2)
