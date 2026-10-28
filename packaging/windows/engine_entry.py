# Private engine entry used only by the full Syllune Windows installer.
# The Electron shell supplies its resource paths; model setup runs in the hub.
# Freeze support prevents multiprocessing from relaunching the application.

import multiprocessing
# ONNX must initialize before Windows Runtime's media bindings in this build.
# The reverse order crashes inside native ONNX initialization (0xC0000005).
import onnxruntime  # noqa: F401

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from syllune.main import main
    main()
