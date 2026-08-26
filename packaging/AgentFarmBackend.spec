from pathlib import Path
import sys

project_root = Path(SPECPATH).parent

# Conda keeps the runtime DLLs in ``Library/bin`` instead of beside the Python
# extension modules. PyInstaller can see the ``.pyd`` files but does not infer
# this directory for the standard-library modules imported by the daemon.
python_runtime_bin = Path(sys.base_prefix) / "Library" / "bin"
python_runtime_dll_names = (
    "libexpat.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "liblzma.dll",
    "libbz2.dll",
    "libmpdec-4.dll",
    "ffi.dll",
    "sqlite3.dll",
)
python_runtime_binaries = [
    (str(python_runtime_bin / name), ".")
    for name in python_runtime_dll_names
    if (python_runtime_bin / name).exists()
]

analysis = Analysis(
    [str(project_root / "packaging" / "agent_farm_backend_entry.py")],
    pathex=[str(project_root)],
    binaries=python_runtime_binaries,
    # The WinUI desktop talks to an API-only loopback server. Browser-console
    # HTML/CSS/JS stays in the Python source distribution and is intentionally
    # absent from the native MSIX backend.
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # These optional pypdf image/data-analysis integrations are not used by
    # Agent Farm's text attachment pipeline. Excluding them removes ~36 MB of
    # native modules and avoids PRI treating Python ABI suffixes as qualifiers.
    excludes=["webview", "PIL", "numpy", "yaml"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AgentFarmBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="AgentFarmBackend",
)
