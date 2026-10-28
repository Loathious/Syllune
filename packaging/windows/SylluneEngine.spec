# Bundle the Python engine and its runtime; Electron is packaged separately.
# CPU speech works without CUDA libraries, system Python, Git, or Ollama.
# Model weights and all per-user data are deliberately absent from the bundle.

from pathlib import Path
from importlib import metadata
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.building import build_main
from PyInstaller.utils.win32 import versioninfo
import tomllib
import os


# Collect only native inference runtimes, preserving their wheel layout and
# upstream license texts. No Python NVIDIA imports or host CUDA paths leak in.
def collect_nvidia_runtime(mode, requirements_path):
    if mode not in ('required', 'auto', 'none'):
        raise ValueError('SYLLUNE_GPU_RUNTIME must be required, auto, or none')
    if mode == 'none':
        return [], []
    distributions = {
        'nvidia-cublas-cu12': 'cublas',
        'nvidia-cudnn-cu12': 'cudnn',
        'nvidia-cuda-runtime-cu12': 'cuda_runtime',
        'nvidia-cuda-nvrtc-cu12': 'cuda_nvrtc',
    }
    try:
        # A pin may carry an environment marker ("==1.0; sys_platform == 'win32'"),
        # which is not part of the version.
        pins = dict(line.split(';', 1)[0].strip().split('==', 1) for line in
                    Path(requirements_path).read_text(encoding='utf-8').splitlines()
                    if '==' in line and not line.lstrip().startswith('#'))
        binaries, notices = [], []
        for name, package in distributions.items():
            distribution = metadata.distribution(name)
            if distribution.version != pins.get(name):
                raise RuntimeError(f'{name} version {distribution.version} differs from pinned version {pins.get(name)}')
            dll_count = notice_count = 0
            for file in distribution.files or []:
                relative = Path(file)
                source = Path(distribution.locate_file(file))
                # NVBLAS is a separate BLAS interception tool; CTranslate2 uses
                # cuBLAS directly. Keep all cuDNN/NVRTC components for lazy loads.
                if (relative.parent.as_posix() == f'nvidia/{package}/bin'
                        and relative.suffix.lower() == '.dll'
                        and not relative.name.lower().startswith('nvblas')):
                    if not source.is_file():
                        raise RuntimeError(f'{name} runtime DLL missing: {relative}')
                    binaries.append((str(source), f'nvidia/{package}/bin'))
                    dll_count += 1
                elif ('licenses' in relative.parts or
                      relative.name.lower().startswith(('license', 'notice', 'copying'))):
                    if not source.is_file():
                        raise RuntimeError(f'{name} license file missing: {relative}')
                    notice_parts = relative.parts
                    suffix = (Path(*notice_parts[notice_parts.index('licenses') + 1:])
                              if 'licenses' in notice_parts else Path(relative.name))
                    destination = Path('licenses/nvidia') / name / suffix.parent
                    notices.append((str(source), destination.as_posix()))
                    notice_count += 1
            if not dll_count or not notice_count:
                raise RuntimeError(f'{name} has incomplete runtime DLLs or license files')
        return binaries, notices
    except (metadata.PackageNotFoundError, OSError, RuntimeError) as error:
        message = f'NVIDIA runtime unavailable: {error}. Install packaging/windows/requirements-runtime.txt or build with -GpuRuntime None.'
        if mode == 'required':
            raise RuntimeError(message) from error
        print(f'WARNING: {message} Building CPU-only engine.')
        return [], []

# PyInstaller imports every collected package in one child process to find DLL
# dependencies. Importing onnxruntime after winrt crashes that process (native
# 0xC0000005), so onnxruntime is moved to the front of the list it imports.
_find_binary_dependencies = build_main.find_binary_dependencies


def _onnxruntime_first(binaries, import_packages, *args, **kwargs):
    import_packages = sorted(import_packages, key=lambda name: name.split('.')[0] != 'onnxruntime')
    return _find_binary_dependencies(binaries, import_packages, *args, **kwargs)


build_main.find_binary_dependencies = _onnxruntime_first

root = Path(SPECPATH).parents[1]

# Full version metadata (publisher, product, description) on the engine exe.
# Unsigned executables with empty metadata are what antivirus heuristics flag
# most often, alongside UPX packing, which stays off below.
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
numbers = tuple(int(part) for part in version.split('.')) + (0,)
version_info = versioninfo.VSVersionInfo(
    ffi=versioninfo.FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[versioninfo.StringFileInfo([versioninfo.StringTable('040904B0', [
        versioninfo.StringStruct('CompanyName', 'Loathious'),
        versioninfo.StringStruct('FileDescription', 'Syllune speech engine'),
        versioninfo.StringStruct('FileVersion', version),
        versioninfo.StringStruct('InternalName', 'SylluneEngine'),
        versioninfo.StringStruct('LegalCopyright', 'Copyright (c) 2026 Loathious'),
        versioninfo.StringStruct('OriginalFilename', 'SylluneEngine.exe'),
        versioninfo.StringStruct('ProductName', 'Syllune'),
        versioninfo.StringStruct('ProductVersion', version)])]),
        versioninfo.VarFileInfo([versioninfo.VarStruct('Translation', [1033, 1200])])])
datas = collect_data_files('syllune')
for package in ('faster_whisper', 'ten_vad', 'sherpa_onnx', 'sounddevice', 'certifi'):
    datas += collect_data_files(package)
binaries = []
gpu_binaries, gpu_notices = collect_nvidia_runtime(
    os.environ.get('SYLLUNE_GPU_RUNTIME', 'auto').lower(),
    root / 'packaging/windows/requirements-runtime.txt')
binaries += gpu_binaries
datas += gpu_notices
for package in ('ctranslate2', 'onnxruntime', 'sherpa_onnx', '_sounddevice_data'):
    binaries += collect_dynamic_libs(package)
hiddenimports = collect_submodules('syllune') + [
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
    console=False, version=version_info, icon=str(root / 'overlay/assets/syllune.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SylluneEngine')
