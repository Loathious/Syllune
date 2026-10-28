# Third-party notices

Syllune is proprietary software (see [LICENSE](LICENSE)). It contains code
that was originally published under the MIT License, and it runs on
open-source components that keep their own licenses. Those notices are
reproduced here as their licenses require.

## Code Syllune was built from

Parts of Syllune's source started as the open-source projects
whisper-key-local and whisper-local. That code is used under the following
license:

```
MIT License

Copyright (c) 2025 Pin Wang (original whisper-key-local)
Copyright (c) 2026 Rohit Burani (whisper-local fork and modifications)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Components Syllune runs on

Each of these is used under its own license; the linked project states its
terms. The installer also carries the license files these packages ship with.

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
  [CTranslate2](https://github.com/OpenNMT/CTranslate2): speech recognition
- [Whisper](https://github.com/openai/whisper) and
  [KB-Whisper](https://huggingface.co/KBLab) models: downloaded on first use
- [TEN VAD](https://github.com/TEN-framework/ten-vad) and
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx): voice detection
- [Electron](https://www.electronjs.org): the pill and the app window
- [sounddevice](https://github.com/spatialaudio/python-sounddevice),
  [soundcard](https://github.com/bastibe/SoundCard),
  [pystray](https://github.com/moses-palmer/pystray),
  [Pillow](https://python-pillow.org),
  [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/),
  [pywin32](https://github.com/mhammond/pywin32) and the other Python
  packages listed in `pyproject.toml`
- [Ollama](https://ollama.com): optional, installed separately by you
