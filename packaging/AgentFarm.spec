from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH).parent
icon_path = project_root / "build" / "packaging" / "agent-farm.ico"
version_path = project_root / "packaging" / "version_info.txt"

analysis = Analysis(
    [str(project_root / "packaging" / "agent_farm_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=collect_data_files("agent_farm", includes=["web/*"]),
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="AgentFarm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=str(version_path),
)
