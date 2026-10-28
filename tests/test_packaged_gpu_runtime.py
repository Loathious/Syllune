# Exercise the installed engine's CUDA search paths and runtime collection.
# These tests use fake wheel files, so no GPU, CUDA installation, or freezer
# execution is necessary to prove that a release is self-contained.
import ast
import gc
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
import weakref

import tempfile
import unittest
from unittest import mock

from syllune import utils


def load_runtime_collector():
    """Load the spec's pure collector without starting PyInstaller."""
    spec = Path(__file__).parents[1] / 'packaging/windows/SylluneEngine.spec'
    tree = ast.parse(spec.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'collect_nvidia_runtime']
    namespace = {'Path': Path, 'metadata': metadata}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(spec), 'exec'), namespace)
    return namespace['collect_nvidia_runtime']


class PackagedGpuRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def patch(self, target, name, value, **kwargs):
        return self.enterContext(mock.patch.object(target, name, value, **kwargs))

    def test_frozen_lookup_uses_bundle_only(self):
        tmp_path = self.tmp_path
        bundle = tmp_path / 'installed' / '_internal'
        expected = bundle / 'nvidia/cudnn/bin'
        expected.mkdir(parents=True)
        developer = tmp_path / 'developer'
        (developer / 'nvidia/cublas/bin').mkdir(parents=True)
        self.patch(utils.sys, 'frozen', True, create=True)
        self.patch(utils.sys, '_MEIPASS', str(bundle), create=True)
        self.patch(utils.site, 'getsitepackages', lambda: [str(developer)])
        self.patch(utils.site, 'getusersitepackages', lambda: str(developer))
        assert utils.find_nvidia_dll_dirs() == [str(expected)]


    def test_frozen_cpu_bundle_does_not_borrow_developer_cuda(self):
        tmp_path = self.tmp_path
        (tmp_path / 'nvidia/cublas/bin').mkdir(parents=True)
        self.patch(utils.sys, 'frozen', True, create=True)
        self.patch(utils.sys, '_MEIPASS', str(tmp_path / 'cpu-only'), create=True)
        self.patch(utils.site, 'getsitepackages', lambda: [str(tmp_path)])
        self.patch(utils.site, 'getusersitepackages', lambda: str(tmp_path))
        assert utils.find_nvidia_dll_dirs() == []


    def test_dll_directory_handles_survive_and_registration_is_idempotent(self):
        tmp_path = self.tmp_path
        class Handle:
            pass

        handles = []

        def register(directory):
            handle = Handle()
            handles.append(weakref.ref(handle))
            return handle

        self.patch(utils.sys, 'platform', 'win32')
        self.patch(utils, '_nvidia_dll_directory_handles', {}, create=True)
        self.patch(utils, 'find_nvidia_dll_dirs', lambda: [str(tmp_path)])
        self.patch(utils.os, 'add_dll_directory', register, create=True)
        self.enterContext(mock.patch.dict(utils.os.environ, {'PATH': 'existing'}))
        utils.setup_nvidia_dll_path()
        gc.collect()
        assert handles[0]() is not None
        utils.setup_nvidia_dll_path()
        assert len(handles) == 1
        assert utils.os.environ['PATH'].split(utils.os.pathsep) == [str(tmp_path), 'existing']


    def test_required_gpu_build_fails_for_missing_runtime(self):
        tmp_path = self.tmp_path
        def missing(name):
            raise metadata.PackageNotFoundError(name)

        self.patch(metadata, 'distribution', missing)
        with self.assertRaisesRegex(RuntimeError, 'NVIDIA runtime'):
            load_runtime_collector()('required', tmp_path / 'lock.txt')


    def test_cpu_build_never_looks_for_nvidia_wheels(self):
        tmp_path = self.tmp_path
        def unexpected(name):
            self.fail('CPU-only build inspected NVIDIA distribution')

        self.patch(metadata, 'distribution', unexpected)
        assert load_runtime_collector()('none', tmp_path / 'lock.txt') == ([], [])


    def test_collection_keeps_dll_layout_and_each_license(self):
        tmp_path = self.tmp_path
        names = {
            'nvidia-cublas-cu12': ('cublas', 'cublas64_12.dll'),
            'nvidia-cudnn-cu12': ('cudnn', 'cudnn64_9.dll'),
            'nvidia-cuda-runtime-cu12': ('cuda_runtime', 'cudart64_12.dll'),
            'nvidia-cuda-nvrtc-cu12': ('cuda_nvrtc', 'nvrtc64_120_0.dll'),
        }
        distributions = {}
        for name, (folder, dll) in names.items():
            files = [Path(f'nvidia/{folder}/bin/{dll}'),
                     Path(f'{name}.dist-info/licenses/License.txt')]
            for file in files:
                target = tmp_path / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('fixture')
            distributions[name] = SimpleNamespace(version='1.0', files=files,
                                                   locate_file=lambda path: tmp_path / path)
        lock = tmp_path / 'requirements.txt'
        lock.write_text('\n'.join(f'{name}==1.0' for name in names))
        self.patch(metadata, 'distribution', distributions.__getitem__)
        binaries, data = load_runtime_collector()('required', lock)
        assert len(binaries) == 4
        assert {destination for _, destination in binaries} == {
            f'nvidia/{folder}/bin' for folder, _ in names.values()}
        assert {destination for _, destination in data} == {
            f'licenses/nvidia/{name}' for name in names}


    def test_required_gpu_build_rejects_wrong_wheel_version(self):
        tmp_path = self.tmp_path
        lock = tmp_path / 'requirements.txt'
        lock.write_text('nvidia-cublas-cu12==1.0')
        self.patch(metadata, 'distribution', lambda name: SimpleNamespace(version='2.0'))
        with self.assertRaisesRegex(RuntimeError, 'version'):
            load_runtime_collector()('required', lock)


    def test_pins_with_environment_markers_match_installed_versions(self):
        lock = self.tmp_path / 'requirements.txt'
        lock.write_text("nvidia-cublas-cu12==1.0; sys_platform == 'win32'")
        dll = self.tmp_path / 'nvidia/cublas/bin/cublas64_12.dll'
        notice = self.tmp_path / 'nvidia_cublas_cu12.dist-info/License.txt'
        for path in (dll, notice):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        files = [Path('nvidia/cublas/bin/cublas64_12.dll'), Path('nvidia_cublas_cu12.dist-info/License.txt')]
        found = SimpleNamespace(version='1.0', files=files, locate_file=lambda path: self.tmp_path / path)
        self.patch(metadata, 'distribution',
                   lambda name: found if name == 'nvidia-cublas-cu12' else SimpleNamespace(version='0'))
        # Only cublas is pinned here, so the next wheel fails on its version,
        # never on cublas's marker.
        with self.assertRaisesRegex(RuntimeError, 'nvidia-cudnn-cu12 version'):
            load_runtime_collector()('required', lock)
