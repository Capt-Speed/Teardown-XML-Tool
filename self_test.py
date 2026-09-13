from pathlib import Path
import struct
from voxel_scale import chunk, dictionary

def fixture(root):
    root.mkdir()
    (root/'info.txt').write_text('name = Automated test fixture\n',encoding='utf-8')
    (root/'script.lua').write_bytes(b'-- preserve bytes\r\nfunction tick() end\r\n')
    vox=(chunk(b'SIZE',struct.pack('<iii',11,12,13))+
         chunk(b'XYZI',struct.pack('<i',3)+bytes([3,4,2,41,5,7,6,99,4,6,5,127]))+
         chunk(b'nTRN',struct.pack('<i',0)+dictionary({'_name':'part'})+struct.pack('<iiii',1,-1,0,1)+dictionary({'_r':'4'}))+
         chunk(b'nSHP',struct.pack('<i',1)+dictionary({})+struct.pack('<ii',1,0)+dictionary({}))+
         chunk(b'RGBA',bytes(range(256))*4)+chunk(b'MATL',struct.pack('<i',41)+dictionary({'_type':'_metal','_rough':'0.3'})))
    (root/'part.vox').write_bytes(b'VOX '+struct.pack('<i',150)+chunk(b'MAIN',children=vox))
    (root/'unrelated.xml').write_text('<prefab><vox file="MOD/part.vox" object="part"/></prefab>',encoding='utf-8')
    (root/'unrelated.txt').write_text('must never be copied into export assets',encoding='utf-8')
    source=root/'model.xml'
    source.write_text('<prefab><!-- preserve comment --><script file="MOD/script.lua" tags="script_tag"><group pos="1 2 3" rot="20 30 40"><body dynamic="true" tags="body_tag"><vox file="MOD/part.vox" object="part" tags="shape_tag" pos="2 0 1"><location pos="1 2 3"/></vox><voxbox size="3 5 7" tags="box_tag"/></body></group></script></prefab>',encoding='utf-8')
    return source

def run_self_test(test_ui=False):
    import unittest, tempfile, json, hashlib, time
    from localization import set_language
    import test_native_origin, test_geometry, test_fractional, test_references, test_script_paths, test_selection
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'passed':True})
    modules=(test_native_origin,test_geometry,test_fractional,test_references,test_script_paths,test_selection)
    result=unittest.TestResult()
    for module in modules:unittest.defaultTestLoader.loadTestsFromModule(module).run(result)
    checks.append({'name':'geometry_and_resource_regressions','tests':result.testsRun,'passed':result.wasSuccessful()})
    if not result.wasSuccessful():return {'ok':False,'desktop_access':False,'checks':checks,'errors':[str(x) for x in result.errors+result.failures]}
    root=None;window=None
    try:
        from app_api import execute_job
        from verify_export import verify_export
        with tempfile.TemporaryDirectory(prefix='td-xml-test-') as temporary:
            source=fixture(Path(temporary)/'中文路径 source')
            original={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.parent.iterdir()}
            en=[];out=source.parent/'English.xml'
            report=execute_job(str(source),str(out),progress=en.append)
            check('english_progress_default',bool(en) and all(not any('\u4e00'<=c<='\u9fff' for c in m.replace(source.name,'')) for m in en))
            check('export_independent_voxel_verification',verify_export(out.with_suffix('.report.json'))['ok'])
            for language,expected in (('en','existing source'),('zh','原 XML')):
                try:execute_job('',language=language)
                except ValueError as e:check('localized_error_'+language,expected in str(e))
                else:raise AssertionError('missing input accepted')
            set_language('en')
            if test_ui:
                import os
                os.environ['QT_QPA_PLATFORM']='offscreen'
                from PySide6.QtWidgets import QApplication
                from ui import Window,configure_app
                app=QApplication.instance() or QApplication([]);configure_app(app)
                window=Window();check('default_english',window.language=='en')
                window.source.setText(str(source));window.destination.setText(str(source.parent/'ui.xml'))
                window.switch_language('zh');window.switch_language('en')
                check('language_preserves_paths',window.source.text()==str(source) and window.destination.text()==str(source.parent/'ui.xml'))
                check('ui_never_shown',not window.isVisible())
                window.close();window=None
            check('original_files_unchanged',all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in original.items()))
        return {'ok':True,'version':'1.2.1','desktop_access':False,'ui':'Qt offscreen widget API' if test_ui else 'not loaded','checks':checks}
    except Exception as error:
        return {'ok':False,'desktop_access':False,'checks':checks,'error':str(error)}
    finally:
        set_language('en')
        if root is not None:
            if window and window.worker:window.cancel();window.worker.join(30)
            root.destroy()

