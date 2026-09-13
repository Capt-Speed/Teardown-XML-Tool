import unittest,tempfile,pathlib,re,hashlib,xml.etree.ElementTree as ET
from scale_package import ScalePackage
from mirror_package import resolve_path

class ScriptPathTests(unittest.TestCase):
    def test_nested_includes_spaces_unicode_and_isolation(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t)/'Content Mod';root.mkdir();(root/'info.txt').write_text('name=test')
            folder=root/'Soviet Union'/'Cold War';folder.mkdir(parents=True)
            (folder/'extra helper.lua').write_text('function helper() return 1 end')
            (folder/'初始化 code.lua').write_text('#include "./extra helper.lua"\nfunction VehicleInit(v,vehicle,weapons) SetFloat("test.caliber",weapons.main.Caliber) end')
            (folder/'vehicle code.lua').write_text('#include "./初始化 code.lua"\nWeapons={main={Caliber=125}}\nfunction init() VehicleInit(FindVehicle("TABS"),{},Weapons);SetTag(1,"TABSINIT") end',encoding='utf-8')
            source=folder/'Tank original.xml';source.write_text('<prefab><script file="MOD/Soviet Union/Cold War/vehicle code.lua"><vehicle tags="TABS Name=KeepMe"/></script></prefab>')
            originals={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            out=folder/'Tank two times.xml';report=ScalePackage(source,out,2).build()
            script=resolve_path(out,ET.parse(out).find('script').get('file'),'')
            visited=set()
            def visit(path):
                visited.add(path)
                text=path.read_text(encoding='utf-8')
                for m in re.finditer(r'(?m)^\s*#include\s+["\']([^"\']+)["\']',text):
                    ref=m[1];self.assertFalse(re.search(r'\s',ref));self.assertTrue(ref.startswith('./'))
                    # Match the game's whitespace boundary, not a permissive
                    # full quoted-string preprocessor used by earlier tests.
                    token=ref.split()[0];dep=(path.parent/token).resolve()
                    self.assertTrue(dep.is_file());self.assertTrue(dep.is_relative_to(folder/report['asset_namespace']))
                    visit(dep)
            visit(script);self.assertEqual(len(visited),3)
            self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in originals.items()))
            self.assertEqual(ET.parse(out).find('.//vehicle').get('tags'),'TABS Name=KeepMe')

if __name__=='__main__':unittest.main(verbosity=2)
