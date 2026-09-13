# Teardown XML Tool

[English](#english) · [简体中文](#简体中文)

| Download | Link |
| --- | --- |
| **Windows app / Windows 软件** | **[Download EXE / 下载 EXE](https://github.com/Capt-Speed/Teardown-XML-Tool/releases/latest/download/TeardownXML_v1.2.1.exe)** |
| **Source code / 源代码** | **[Download source ZIP / 下载源码 ZIP](https://github.com/Capt-Speed/Teardown-XML-Tool/archive/refs/tags/v1.2.1.zip)** |
| Release notes / 版本说明 | [Releases](https://github.com/Capt-Speed/Teardown-XML-Tool/releases) |

## English

A Windows desktop tool for enlarging and mirroring Teardown XML/VOX models. Version **1.2.1** uses the original light Qt interface, with an English / Simplified Chinese selector in the upper-right corner. English is the default.

### Run the app

Download the EXE above and double-click it. The app is a single-file, native-compiled executable; no Python installation is required. Required runtime libraries are extracted to a temporary folder when it starts.

1. Choose the source XML. The new XML must have a different name in the same folder.
2. Select Enlarge or Mirror. Choose the integer multiplier or XML mirror axis.
3. Optionally set the Teardown installation folder to resolve built-in or workshop dependencies.
4. Choose Check files, then Export. The log and report show progress and limitations.

Mirror directions are defined in **source XML coordinates**, through the XML origin: X reflects across YZ, Y across XZ, and Z across XY. These are not necessarily a rotated vehicle's left/right or forward/back directions. Hover over the axis selector for a reminder.

To exclude a part's shape from mirroring, right-click the axis selector or press **Ctrl+M**. A checked part's XML pivot moves to the reflected position while its original world orientation, shape and internal subtree offsets are preserved. Checking a parent includes its entire subtree. Select children individually if each child should have its own reflected pivot position.

### Export behavior

- Saves a new XML beside the original, with unique names for changed resources. Does not create a new map or copy unrelated map contents.
- Preserves original XML, shared VOX and scripts; transformed dependencies use an isolated asset namespace.
- An integer `original scale × multiplier` is baked by replicating voxels, with output geometry `scale=1`.
- A fractional product is retained as the multiplied XML `scale`. Example: `1.1 × 2 = 2.2`.
- Large VOX scenes are represented using multiple valid models of at most 256 cells per dimension in the VOX file.
- Preserves voxel material metadata, ordinary tags and XML hierarchy while transforming supported coordinates and dimensions.
- Includes TABS-specific parameter handling. Applicable lengths follow the multiplier; mass and explosive charge default to its cube. Original scripts are not overwritten.

This is an unofficial tool. Static geometry checks cannot establish in-game collision, destruction, track assembly or script initialization behavior. Runtime-generated Lua geometry and global framework constants may require separate handling. Version 1.2.1 was packaged without rerunning the test suite; do not interpret its release as a new in-game validation.

### Run from source

Use Python **3.12** on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe studio.py
```

### Build a single EXE

Install Visual Studio / Build Tools with **Desktop development with C++**, MSVC and a Windows SDK, then:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

The EXE is written to `dist/TeardownXML_v1.2.1.exe`. The build script does not run tests. Native compilation increases reverse-engineering effort, but cannot prevent it completely. The source in this repository is intentionally public.

### Command line and optional tests

The GUI executable has no console window; pass `--report` to save its results. Replace `python studio.py` with the EXE path when using the packaged app.

```powershell
python studio.py --inspect --source "model.xml" --report check-results.json
python studio.py --list-parts --source "model.xml" --report parts-results.json
python studio.py --cli --source "model.xml" --factor 2 --report export-results.json
python studio.py --cli --source "model.xml" --operation mirror --axis X --keep-node 0/0/1 --report mirror-results.json
python studio.py --verify-export "model_x2.report.json" --report verify-results.json
python studio.py --self-test --report test-results.json
python studio.py --self-test --test-ui --report ui-test-results.json
python studio.py --licenses --report licenses-results.json
```

`--keep-node` can be repeated; obtain IDs from `--list-parts`. Add `--language zh` for Chinese messages and `--game "PATH"` for game dependencies. Tests use synthetic fixtures and an offscreen Qt platform; they do not capture or control the desktop. Tests run only when explicitly invoked.

### Repository contents and dependencies

`studio.py` is the entry point; `ui.py` contains the original Qt interface; `app_api.py` is the screen-free job API. The scale, mirror, VOX, selection and TABS modules implement export behavior. `test_*.py`, `self_test.py`, `engine_reference.py` and `audit_latest.py` provide optional offline checks and generated fixtures.

Game assets, user models, private workspace files, compiler caches and build dependencies are not included. PySide6/Qt, Python, Nuitka and other dependencies retain their respective licenses; see their upstream distributions and `notices.py` for bundled runtime notices. This repository does not grant rights to Teardown or TABS assets.

## 简体中文

这是用于 Teardown XML / VOX 的放大与镜像工具。**1.2.1 恢复原来的浅色 Qt 界面**，仅在右上角增加语言切换，默认英文。

### 下载与使用

- **只使用软件：**点击顶部“下载 EXE”，下载后直接双击，无需安装 Python。
- **查看或修改代码：**点击顶部“下载源码 ZIP”，或者克隆本仓库。
- **查看版本：**进入 Releases，EXE 和 GitHub 自动生成的源码压缩包可分别下载。

选择原 XML，给新 XML 使用同文件夹中的新名称，然后选择放大倍数或镜像轴，再检查、导出。所需的游戏资源可以通过“游戏目录”字段定位。

镜像轴是原 XML 的坐标轴，镜面通过 XML 原点，不一定对应旋转后车辆自身的左右或前后。悬停轴选择框可看说明。**右键轴选择框或按 Ctrl+M** 可选择保留形状的部件：位置跟随对称移动，但保留原世界朝向、形状和子树内部位移。勾选父级会包含所有子级；需要各子部件分别对称移动时，应分别选择子部件。

### 文件与放大规则

- 在原文件夹生成新 XML，修改资源使用独立名称，不覆盖原 XML、共享 VOX 或原脚本，不创建新地图。
- 原 `scale × 倍数` 为整数时复制体素，输出几何 `scale=1`；为小数时保留乘积，例如 `1.1 × 2 = 2.2`。
- 大型 VOX 可在同一文件内拆成多个不超过 256 格的模型，并保留材质和普通 tags。
- 包含 XML 层级、坐标、适用尺寸及 TABS 参数处理；质量和装药默认按体积增加。

这是非官方工具。离线检查不能证明实际游戏中的碰撞、破坏、履带或脚本初始化效果；动态生成几何和框架全局常量可能需要单独处理。**1.2.1 按要求直接打包，没有重新运行测试。**

### 开发与构建

使用 Windows + Python 3.12。按上方 Run from source 命令创建虚拟环境、安装 `requirements.txt`，再运行 `studio.py`。构建 EXE 时另装 Visual Studio C++ 工具链和 `requirements-build.txt`，运行 `build.ps1`。构建脚本不会自动测试。

上方列出了命令行与可选测试接口。它们不截图，也不控制鼠标键盘。本仓库只包含工具源码、说明和合成测试，不包含游戏资产、用户模型或工作区缓存。
