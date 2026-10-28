# Bundle the Python engine and its runtime; Electron is packaged separately.
# CPU speech works without CUDA libraries, system Python, Git, or Ollama.
# Model weights and all per-user data are deliberately absent from the bundle.

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.building import build_main

# PyInstaller imports every collected package in one child process to find DLL
# dependencies. Importing onnxruntime after winrt crashes that process (native
# 0xC0000005), so onnxruntime is moved to the front of the list it imports.
_find_binary_dependencies = build_main.find_binary_dependencies


def _onnxruntime_first(binaries, import_packages, *args, **kwargs):
    import_packages = sorted(import_packages, key=lambda name: name.split('.')[0] != 'onnxruntime')
    return _find_binary_dependencies(binaries, import_packages, *args, **kwargs)


build_main.find_binary_dependencies = _onnxruntime_first

root = Path(SPECPATH).parents[1]
datas = collect_data_files('whisper_key')
for package in ('faster_whisper', 'ten_vad', 'sherpa_onnx', 'sounddevice', 'certifi'):
    datas += collect_data_files(package)
binaries = []
for package in ('ctranslate2', 'onnxruntime', 'sherpa_onnx', '_sounddevice_data'):
    binaries += collect_dynamic_libs(package)
hiddenimports = collect_submodules('whisper_key') + [
    'pystray._win32', 'win32api', 'win32gui', 'win32con', 'win32event', 'win32process',
    'win32com.client', 'soundcard.mediafoundation', '_sounddevice_data',
    'winrt.windows.media.control', 'winrt.windows.foundation', 'winrt.windows.foundation.collections',
]
a = Analysis([str(root / 'packaging/windows/engine_entry.py')], pathex=[str(root / 'src')],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    excludes=['torch', 'tensorflow', 'transformers', 'matplotlib', 'pandas', 'scipy', 'IPython', 'pytest', 'nvidia'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SylluneEngine',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, icon=str(root / 'overlay/assets/syllune.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SylluneEngine')
