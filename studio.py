"""Native app entry point and unattended interfaces."""
import argparse
import json
import sys
from pathlib import Path

def write_result(result,report):
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if report:
        path=Path(report);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
    if sys.stdout is not None:print(text)

def main(argv=None):
    parser=argparse.ArgumentParser(description='Teardown XML Tool 1.2.1')
    modes=parser.add_mutually_exclusive_group()
    for flag in ('cli','inspect','self-test','list-parts','licenses'):modes.add_argument('--'+flag,action='store_true')
    modes.add_argument('--verify-export',metavar='REPORT_JSON')
    parser.add_argument('--test-ui',action='store_true')
    parser.add_argument('--source');parser.add_argument('--output');parser.add_argument('--report')
    parser.add_argument('--factor',type=int,default=2)
    parser.add_argument('--operation',choices=['scale','mirror'],default='scale')
    parser.add_argument('--axis',choices=['X','Y','Z'],default='X')
    parser.add_argument('--keep-node',action='append',default=[])
    parser.add_argument('--no-tabs-parameters',action='store_true')
    parser.add_argument('--mass-mode',choices=['volume','linear'],default='volume')
    parser.add_argument('--game',default='')
    parser.add_argument('--language',choices=['en','zh'],default='en')
    a=parser.parse_args(argv)
    from localization import set_language
    set_language(a.language)
    try:
        if a.licenses:
            from notices import NOTICES
            write_result({'licenses':NOTICES},a.report);return 0
        if a.self_test:
            from self_test import run_self_test
            result=run_self_test(a.test_ui);write_result(result,a.report);return 0 if result['ok'] else 1
        if a.list_parts:
            from selection import inspect_parts
            write_result(inspect_parts(a.source),a.report);return 0
        if a.verify_export:
            from verify_export import verify_export
            result=verify_export(a.verify_export);write_result(result,a.report);return 0 if result['ok'] else 1
        from app_api import execute_job
        if a.cli or a.inspect:
            result=execute_job(a.source,a.output or '',a.factor,a.game,a.operation,a.axis,inspect=a.inspect,
                    tabs_parameters=not a.no_tabs_parameters,mass_mode=a.mass_mode,preserve_nodes=a.keep_node,language=a.language)
            write_result({'ok':True,'result':result},a.report);return 0
        from PySide6.QtWidgets import QApplication
        from ui import Window, configure_app
        app=QApplication([sys.argv[0]]);configure_app(app)
        window=Window(a.language)
        if a.source:window.source.setText(a.source);window.default_output()
        window.show();return app.exec()
    except Exception as error:
        write_result({'ok':False,'error':str(error),'error_type':type(error).__name__},a.report);return 1

if __name__=='__main__':sys.exit(main())
