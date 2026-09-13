"""Public, screen-free job API used by both the GUI and command line."""
from pathlib import Path
import hashlib
from localization import set_language, choose
from scale_package import ScalePackage

VERSION = '1.2.1'

def suggest_output(source, factor=2, operation='scale', axis='X'):
    if not source:return ''
    p=Path(source)
    suffix=f'_x{factor}' if operation=='scale' else f'_mirror_{axis}'
    target=p.with_name(p.stem+suffix+'.xml')
    i=2
    while target.exists() or target.with_suffix('.report.json').exists():
        target=p.with_name(p.stem+suffix+f'_{i}.xml');i+=1
    return str(target)

def execute_job(source, output='', factor=2, game='', operation='scale', axis='X',
                inspect=False, tabs_parameters=True, mass_mode='volume', preserve_nodes=(),
                language='en', source_hash='', progress=lambda message:None, cancel=lambda:False):
    set_language(language)
    if not source or not Path(source).is_file():
        raise ValueError(choose('Choose an existing source XML file.','请选择存在的原 XML 文件。'))
    if operation not in ('scale','mirror') or axis not in ('X','Y','Z'):
        raise ValueError(choose('Invalid operation or axis.','操作或轴无效。'))
    if source_hash and hashlib.sha256(Path(source).read_bytes()).hexdigest()!=source_hash:
        raise ValueError(choose('The source XML changed. Reload the parts tree.','原 XML 已改变，请重新加载部件树。'))
    output=output or suggest_output(source,factor,operation,axis)
    package=ScalePackage(source,output,factor,game,progress,cancel,
                         mirror_axis=axis if operation=='mirror' else None,
                         tabs_parameters=tabs_parameters,mass_mode=mass_mode,
                         preserve_nodes=preserve_nodes if operation=='mirror' else ())
    if inspect:
        package.preflight(validate_scale=operation=='scale')
        return {'inspection':True, 'source':str(package.source),'output':str(package.destination),
                'tabs_detected':package.tabs_detected,'details':package.report}
    return package.build()
