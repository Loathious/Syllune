# Private engine entry used only by the full Syllune Windows installer.
# The Electron shell supplies its resource paths; model setup runs in the hub.
# Freeze support prevents multiprocessing from relaunching the application.

import multiprocessing
import os
import sys
from pathlib import Path

# Windowless PyInstaller streams can be None before main() is imported. Native
# and import-time failures must leave diagnostics for the shell's Retry screen.
log_dir = Path(os.environ.get('APPDATA') or Path.home()) / 'Syllune'
log_dir.mkdir(parents=True, exist_ok=True)
log_stream = open(log_dir / 'engine-startup.log', 'a', encoding='utf-8', buffering=1)
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
if sys.stderr is None:
    sys.stderr = log_stream
import faulthandler
faulthandler.enable(file=log_stream)
# ONNX must initialize before Windows Runtime's media bindings in this build.
# The reverse order crashes inside native ONNX initialization (0xC0000005).
import onnxruntime  # noqa: F401

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from syllune.main import main
    raise SystemExit(main())
