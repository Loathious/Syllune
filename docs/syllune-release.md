# Release Syllune

The Windows release is the full desktop installer, not the inherited Python-only executable. The internal distribution name remains `whisper-local` for existing package integrations, but this fork never publishes it to PyPI.

1. Run the Python and Node suites and the renderer checks. Physically test microphone/hotkey/insertion, all three docks, quality changes, setup, and an installed first launch.
2. Review version consistency in Python, `brand.py`, `overlay/brand.json`, `overlay/package.json`, and `CITATION.cff`.
3. Configure the owned public repository using `tools/configure_release.py owner/repo`. This updates the two runtime release configurations. Never target the original upstream product.
4. Build using `packaging/windows/build.ps1`. Verify the generated installer in an isolated install directory and inspect its SHA-256 checksum. The default build is unsigned.
5. Prepare a clean export using `tools/export_public.py <new-directory>`, and inspect the audit and outgoing files. This deliberately does not copy development Git history.
6. Use a public nickname and GitHub noreply address for new commits. Keep original source attribution in LICENSE/AUTHORS. Create several coherent commits for the implementation.
7. Push only to the dedicated Syllune repository. The release workflow accepts `syllune-v*` tags and manual builds. A manual run builds an artifact; only a matching tag creates a public release. It never publishes to PyPI.

Checksums detect an incomplete or mismatched download; they do not replace code signing or independent provenance verification. Do not claim a signing identity unless the release was signed and verified.
