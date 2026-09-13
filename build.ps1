param([string]$Python = 'python', [int]$Jobs = 8)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $buildArgs = @(
        '-m', 'nuitka', '--mode=onefile', '--enable-plugin=pyside6',
        '--include-qt-plugins=platforms', '--msvc=latest',
        '--windows-console-mode=disable', '--output-dir=dist',
        '--output-filename=TeardownXML_v1.2.1.exe', '--lto=yes',
        '--assume-yes-for-downloads', '--report=compilation-report.xml',
        '--python-flag=no_docstrings', "--jobs=$Jobs",
        '--product-name=Teardown XML Tool', '--file-version=1.2.1',
        '--product-version=1.2.1', 'studio.py'
    )
    & $Python @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "Build failed: exit code $LASTEXITCODE" }
    Get-Item -LiteralPath 'dist/TeardownXML_v1.2.1.exe'
}
finally { Pop-Location }
