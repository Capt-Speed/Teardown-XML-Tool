"""Light desktop UI. Test controls directly; never automate the user's desktop."""
import json
import threading
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPalette, QDesktopServices
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget, QCheckBox
from scale_package import ScalePackage
from ui_text import ui_text, translate_existing
from localization import set_language
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTreeWidget, QTreeWidgetItem
from selection import inspect_parts
VERSION = '1.2.1'
GAME = 'E:\\SteamLibrary\\steamapps\\common\\Teardown'

def suggest_output(source, factor=2, operation='scale', axis='X'):
    source = Path(source).resolve()
    suffix = f'_x{factor}' if operation == 'scale' else '_mirror_' + axis.lower()
    output = source.with_name(source.stem + suffix + '.xml')
    number = 2
    while output.exists() or output.with_suffix('.report.json').exists():
        output = source.with_name(source.stem + suffix + '_' + str(number) + '.xml')
        number += 1
    return str(output)

def configure_app(app):
    app.setStyle('Fusion')
    if app.platformName() == 'offscreen':
        for filename in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
            font = Path('C:/Windows/Fonts') / filename
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Microsoft YaHei UI', 10))
    palette = QPalette()
    colors = {'Window': '#f6f6f6', 'WindowText': '#242424', 'Base': '#ffffff', 'AlternateBase': '#f1f1f1', 'Text': '#242424', 'Button': '#f5f5f5', 'ButtonText': '#242424', 'Highlight': '#2865a5', 'HighlightedText': '#ffffff', 'ToolTipBase': '#ffffff', 'ToolTipText': '#242424', 'PlaceholderText': '#777777'}
    for name, value in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, name), QColor(value))
    for name in ('Text', 'ButtonText', 'WindowText'):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, name), QColor('#888888'))
    app.setPalette(palette)
    app.setStyleSheet('QLineEdit,QSpinBox,QComboBox {min-height:26px;padding:2px 5px;}\n        QPushButton {min-height:28px;padding:2px 12px;}\n        QPlainTextEdit {border:1px solid #c6c6c6;background:white;}\n        QLabel[muted="true"] {color:#626262;}\n        QProgressBar {min-height:12px;max-height:12px;border:1px solid #cdcdcd;background:white;}\n        QProgressBar::chunk {background:#6697c9;}')
from app_api import execute_job

class Worker(QThread):
    update = Signal(str)
    success = Signal(dict)
    error = Signal(str)

    def __init__(self, parameters):
        super().__init__()
        self.parameters = parameters
        self.stop = threading.Event()

    def run(self):
        try:
            self.success.emit(execute_job(**self.parameters, progress=self.update.emit, cancel=self.stop.is_set))
        except Exception as error:
            self.error.emit(str(error))

class Window(QMainWindow):

    def __init__(self, language='en'):
        set_language(language)
        self.language = language
        self.preserved = set()
        self.parts_source = ''
        self.source_hash = ''
        super().__init__()
        self.setWindowTitle(ui_text('Teardown XML 工具  ') + VERSION)
        self.resize(900, 690)
        self.setMinimumSize(780, 610)
        self.setAcceptDrops(True)
        self.worker = None
        self.result = None
        self.inspection_result = None
        self.last_error = None
        self._suggested_output = ''
        main = QWidget()
        self.setCentralWidget(main)
        layout = QVBoxLayout(main)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.source = QLineEdit()
        self.source.setObjectName('source_path')
        self.source.setPlaceholderText(ui_text('选择 XML 文件，也可以拖入文件'))
        self.destination = QLineEdit()
        self.destination.setObjectName('output_path')
        self.destination.setPlaceholderText(ui_text('原文件夹内的新 XML 文件名'))
        self.game = QLineEdit(GAME if Path(GAME).is_dir() else '')
        self.game.setObjectName('game_path')
        self.game.setPlaceholderText(ui_text('Teardown 安装目录，用于读取游戏自带资源'))
        self.browse_source = QPushButton(ui_text('浏览…'))
        self.browse_source.clicked.connect(self.pick_source)
        self.browse_output = QPushButton(ui_text('浏览…'))
        self.browse_output.clicked.connect(self.pick_output)
        self.browse_game = QPushButton(ui_text('浏览…'))
        self.browse_game.clicked.connect(self.pick_game)
        for label, field, button in [(ui_text('模型文件'), self.source, self.browse_source), (ui_text('输出 XML'), self.destination, self.browse_output), (ui_text('游戏目录'), self.game, self.browse_game)]:
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button)
            form.addRow(label, row)
        layout.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(QLabel(ui_text('操作')))
        self.operation = QComboBox()
        self.operation.setObjectName('operation')
        self.operation.addItems([ui_text('整数倍放大'), ui_text('镜像翻转')])
        row.addWidget(self.operation)
        row.addSpacing(14)
        self.factor_label = QLabel(ui_text('倍数'))
        row.addWidget(self.factor_label)
        self.factor = QSpinBox()
        self.factor.setObjectName('factor')
        self.factor.setRange(1, 1000)
        self.factor.setValue(2)
        self.factor.setSuffix(ui_text(' 倍'))
        self.factor.setMinimumWidth(90)
        row.addWidget(self.factor)
        self.axis_label = QLabel(ui_text('方向'))
        row.addWidget(self.axis_label)
        self.axis = QComboBox()
        self.axis.setObjectName('axis')
        self.axis.addItems([ui_text('X 轴'), ui_text('Y 轴'), ui_text('Z 轴')])
        row.addWidget(self.axis)
        row.addStretch()
        layout.addLayout(row)
        self.estimate = QLabel()
        self.estimate.setProperty('muted', True)
        self.estimate.setWordWrap(True)
        layout.addWidget(self.estimate)
        row = QHBoxLayout()
        self.tabs = QCheckBox(ui_text('同步调整 TABS 装甲和弹药'))
        self.tabs.setObjectName('tabs_parameters')
        self.tabs.setChecked(True)
        row.addWidget(self.tabs)
        self.mass = QComboBox()
        self.mass.setObjectName('mass_mode')
        self.mass.addItems([ui_text('质量、装药按体积增长'), ui_text('质量、装药乘相同倍数')])
        row.addWidget(self.mass)
        row.addStretch()
        layout.addLayout(row)
        self.tabs_note = QLabel(ui_text('口径、穿深和 RHA/CA/ERA 按尺寸倍数；CHA、射速、装填时间和初速保留。履带间距与搜索范围自动修正。'))
        self.tabs_note.setWordWrap(True)
        self.tabs_note.setProperty('muted', True)
        layout.addWidget(self.tabs_note)
        note = QLabel(ui_text('原文件夹内另存 XML，新资源独立命名。小数 scale 可直接按倍数相乘，普通 tags 保留。'))
        note.setProperty('muted', True)
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel(ui_text('处理记录')))
        row.addStretch()
        self.report_button = QPushButton(ui_text('查看报告'))
        self.report_button.setObjectName('view_report')
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.show_report)
        row.addWidget(self.report_button)
        layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setObjectName('log')
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlaceholderText(ui_text('检查结果、处理进度和错误详情会显示在这里。'))
        layout.addWidget(self.log, 1)
        self.status = QLabel(ui_text('就绪'))
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setObjectName('progress')
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.check_button = QPushButton(ui_text('检查文件'))
        self.check_button.setObjectName('inspect')
        self.check_button.clicked.connect(lambda: self.start(inspect=True))
        row.addWidget(self.check_button)
        self.open_button = QPushButton(ui_text('打开输出目录'))
        self.open_button.setObjectName('open_output')
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        row.addWidget(self.open_button)
        row.addStretch()
        self.cancel_button = QPushButton(ui_text('取消'))
        self.cancel_button.setObjectName('cancel')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        self.run_button = QPushButton(ui_text('导出'))
        self.run_button.setObjectName('export')
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(lambda: self.start())
        row.addWidget(self.run_button)
        layout.addLayout(row)
        self.controls = [self.source, self.destination, self.game, self.browse_source, self.browse_output, self.browse_game, self.operation, self.factor, self.axis, self.tabs, self.mass, self.check_button, self.run_button]
        self.language_box = QComboBox()
        self.language_box.setObjectName('language_switch')
        self.language_box.addItems(['English', '简体中文'])
        self.language_box.setCurrentIndex(0 if language == 'en' else 1)
        self.language_box.setFixedWidth(112)
        self.menuBar().setCornerWidget(self.language_box, Qt.Corner.TopRightCorner)
        self.language_box.currentIndexChanged.connect(lambda index: self.switch_language('en' if index == 0 else 'zh'))
        self.parts_action = QAction(self)
        self.parts_action.setShortcut('Ctrl+M')
        self.parts_action.triggered.connect(self.select_parts)
        self.axis.addAction(self.parts_action)
        self.axis.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.addAction(self.parts_action)
        self.update_axis_help()
        self.factor.valueChanged.connect(self.changed)
        self.operation.currentIndexChanged.connect(self.changed)
        self.axis.currentIndexChanged.connect(self.default_output)
        self.source.editingFinished.connect(self.default_output)
        self.changed()

    def state(self):
        """Serializable application state, not desktop state."""
        return {'source': self.source.text(), 'output': self.destination.text(), 'operation': 'mirror' if self.operation.currentIndex() else 'scale', 'factor': self.factor.value(), 'axis': self.axis.currentText()[0], 'tabs_parameters': self.tabs.isChecked(), 'mass_mode': 'volume' if self.mass.currentIndex() == 0 else 'linear', 'busy': bool(self.worker and self.worker.isRunning()), 'factor_enabled': self.factor.isEnabled(), 'axis_enabled': self.axis.isEnabled(), 'export_enabled': self.run_button.isEnabled(), 'cancel_enabled': self.cancel_button.isEnabled(), 'open_enabled': self.open_button.isEnabled(), 'status': self.status.text(), 'error': self.last_error, 'result': self.result, 'inspection': self.inspection_result}

    def changed(self):
        mirror = self.operation.currentIndex() == 1
        self.factor.setEnabled(not mirror)
        self.axis.setEnabled(mirror)
        self.tabs.setEnabled(not mirror)
        self.mass.setEnabled(not mirror)
        self.factor.setVisible(not mirror)
        self.factor_label.setVisible(not mirror)
        self.axis.setVisible(mirror)
        self.axis_label.setVisible(mirror)
        k = self.factor.value()
        self.estimate.setText(ui_text('保持原尺寸，沿所选轴翻转。') if mirror else f"{ui_text('尺寸放大 ')}{k}{ui_text(' 倍。整数 scale 乘积复制体素；小数乘积保留为 scale，不再限制倍数。')}")
        self.default_output()

    def default_output(self):
        source = Path(self.source.text())
        if source.is_file() and self.destination.text() in ('', self._suggested_output):
            self._suggested_output = suggest_output(source, self.factor.value(), 'mirror' if self.operation.currentIndex() else 'scale', self.axis.currentText()[0])
            self.destination.setText(self._suggested_output)

    def pick_source(self):
        path, _ = QFileDialog.getOpenFileName(self, ui_text('选择模型文件'), '', 'Teardown XML (*.xml *.prefab)')
        if path:
            self.source.setText(path)
            self.default_output()

    def pick_output(self):
        path, _ = QFileDialog.getSaveFileName(self, ui_text('在原文件夹内另存 XML'), self.destination.text(), 'Teardown XML (*.xml)')
        if path:
            self.destination.setText(path)

    def pick_game(self):
        path = QFileDialog.getExistingDirectory(self, ui_text('选择 Teardown 安装目录'), self.game.text())
        if path:
            self.game.setText(path)

    def dragEnterEvent(self, event):
        if not self.state()['busy'] and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.state()['busy']:
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls() if Path(u.toLocalFile()).suffix.lower() in {'.xml', '.prefab'}]
        if paths:
            self.source.setText(paths[0])
            self.default_output()

    def start(self, inspect=False):
        if self.state()['busy']:
            return
        if not Path(self.source.text()).is_file():
            self.failed(ui_text('请先选择有效的 XML 文件'))
            return
        self.default_output()
        if not inspect and (not self.destination.text().strip()):
            self.failed(ui_text('请填写输出 XML 文件名'))
            return
        if self.preserved and str(Path(self.source.text()).resolve()) != self.parts_source:
            self.preserved.clear()
            self.source_hash = ''
        self.result = None
        self.inspection_result = None
        self.last_error = None
        self.log.clear()
        self.open_button.setEnabled(False)
        self.report_button.setEnabled(False)
        for control in self.controls:
            control.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText(ui_text('正在检查…') if inspect else ui_text('正在导出…'))
        self.worker = Worker({'source': self.source.text(), 'output': self.destination.text(), 'factor': self.factor.value(), 'game': self.game.text(), 'language': self.language, 'preserve_nodes': tuple(self.preserved), 'source_hash': self.source_hash if self.preserved else '', 'operation': 'mirror' if self.operation.currentIndex() else 'scale', 'axis': self.axis.currentText()[0], 'inspect': inspect, 'tabs_parameters': self.tabs.isChecked(), 'mass_mode': 'volume' if self.mass.currentIndex() == 0 else 'linear'})
        self.worker.update.connect(self.message)
        self.worker.success.connect(self.done)
        self.worker.error.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def message(self, text):
        self.status.setText(text if len(text) < 100 else text[:97] + '…')
        self.log.appendPlainText(text)

    def done(self, result):
        if result.get('inspection'):
            self.inspection_result = result
            self.status.setText(ui_text('检查通过'))
            self.log.appendPlainText(result.get('message', ui_text('检查通过')) + ui_text('\n原 XML：') + result['source'])
        else:
            self.result = result
            self.status.setText(ui_text('导出完成'))
            self.log.appendPlainText(ui_text('输出 XML：') + result['output_xml'])
            for warning in result.get('warnings', []):
                self.log.appendPlainText(warning)
            self.open_button.setEnabled(True)
        self.report_button.setEnabled(True)

    def failed(self, error):
        self.last_error = error
        self.status.setText(ui_text('已取消') if ui_text('已取消') in error else ui_text('未完成，详情见处理记录'))
        self.log.appendPlainText(error)

    def finished(self):
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self.result or self.inspection_result else 0)
        for control in self.controls:
            control.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.changed()

    def cancel(self):
        if self.worker:
            self.worker.stop.set()
            self.cancel_button.setEnabled(False)
            self.status.setText(ui_text('正在取消，等待当前步骤结束…'))

    def show_report(self):
        self.log.setPlainText(json.dumps(self.result or self.inspection_result, ensure_ascii=False, indent=2))

    def open_output(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result['output_directory']))

    def closeEvent(self, event):
        if self.state()['busy']:
            self.cancel()
            event.ignore()
        else:
            event.accept()


    def switch_language(self, language):
        if language == self.language: return
        self.language = language
        set_language(language)
        for widget in self.findChildren(QLabel) + self.findChildren(QPushButton) + self.findChildren(QCheckBox):
            widget.setText(translate_existing(widget.text(), language))
        for widget in self.findChildren(QLineEdit) + self.findChildren(QPlainTextEdit):
            widget.setPlaceholderText(translate_existing(widget.placeholderText(), language))
        for widget in (self.operation, self.axis, self.mass):
            widget.blockSignals(True)
            for i in range(widget.count()):widget.setItemText(i, translate_existing(widget.itemText(i), language))
            widget.blockSignals(False)
        self.factor.setSuffix(ui_text(' 倍'))
        self.setWindowTitle(ui_text('Teardown XML 工具  ') + VERSION)
        self.update_axis_help()
        if not self.state()['busy']: self.changed()

    def update_axis_help(self):
        self.parts_action.setText('Choose parts to preserve…' if self.language == 'en' else '选择保留形状的部件…')
        self.axis.setToolTip('XML axes through origin: X → −X (YZ plane), Y → −Y (XZ), Z → −Z (XY).\nRight-click or Ctrl+M: keep selected parts’ shape and world orientation; reflect their position.' if self.language == 'en' else 'XML 原点坐标轴：X → −X（YZ 镜面），Y → −Y（XZ），Z → −Z（XY）。\n右键或 Ctrl+M 选择部件：保留形状和世界朝向，只对称移动位置。')

    def select_parts(self):
        if self.state()['busy']: return
        try: data = inspect_parts(self.source.text())
        except Exception as error: self.failed(str(error)); return
        source = str(Path(self.source.text()).resolve())
        selected = self.preserved if source == self.parts_source and data['sha256'] == self.source_hash else set()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.parts_action.text())
        dialog.resize(730,480)
        layout = QVBoxLayout(dialog)
        note = QLabel('Check parts to keep their shape and world orientation. Their pivot positions follow the reflection. A checked parent preserves its entire subtree.' if self.language == 'en' else '勾选不镜像的部件：位置跟随对称移动，保留世界朝向和形状。勾选父级会保留整个子树的内部关系。')
        note.setWordWrap(True); layout.addWidget(note)
        tree = QTreeWidget(); tree.setHeaderLabels(['Part / name','Position','Rotation'] if self.language=='en' else ['部件 / 名称','位置','旋转'])
        items = {}
        for row in data['parts']:
            item = QTreeWidgetItem([row['kind'] + '  ' + row['name'], row['pos'], row['rot']])
            parent = items.get(row['parent'])
            if parent:parent.addChild(item)
            else:tree.addTopLevelItem(item)
            if row['selectable']:item.setCheckState(0, Qt.CheckState.Checked if row['path'] in selected else Qt.CheckState.Unchecked)
            item.setToolTip(0,row['tags'])
            items[row['path']] = item
        tree.expandToDepth(2);tree.setColumnWidth(0,380);layout.addWidget(tree)
        buttons = QDialogButtonBox()
        ok = buttons.addButton('OK' if self.language=='en' else '确定',QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('Cancel' if self.language=='en' else '取消',QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.preserved = {row['path'] for row in data['parts'] if row['selectable'] and items[row['path']].checkState(0)==Qt.CheckState.Checked}
            self.parts_source=source;self.source_hash=data['sha256']


def render_widget(window, path):
    """Paint this widget into memory; no screen, window capture or OS input."""
    window.ensurePolished()
    window.centralWidget().layout().activate()
    QApplication.processEvents()
    canvas = QImage(window.size(), QImage.Format.Format_ARGB32)
    canvas.fill(QColor('white'))
    painter = QPainter(canvas)
    from PySide6.QtCore import QPoint
    window.render(painter, QPoint(0, 0))
    painter.end()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(path)):
        raise OSError(ui_text('无法保存界面预览'))
