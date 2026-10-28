# Release Syllune

The Windows release is the full desktop installer. The Python package is named `syllune` and is never published to PyPI.

1. Run the Python and Node suites and the renderer checks. Physically test microphone/hotkey/insertion, all three docks, quality changes, setup, and an installed first launch.
2. Review version consistency in Python, `brand.py`, `overlay/brand.json`, and `overlay/package.json`.
3. Configure the owned public repository using `tools/configure_release.py owner/repo`. This updates the two runtime release configurations. Never target another product's repository.
4. Build using `packaging/windows/build.ps1 -Signed -CertificateThumbprint <thumbprint>` for a signed release (see below). Verify the generated installer in an isolated install directory and inspect its SHA-256 checksum. Running without `-Signed` produces an unsigned local/debug build.
5. Prepare a clean export using `tools/export_public.py <new-directory>`, and inspect the audit and outgoing files. This deliberately does not copy development Git history.
6. Use a public nickname and GitHub noreply address for new commits. Keep the upstream MIT notice in THIRD_PARTY_NOTICES.md; LICENSE is Syllune's own. Create several coherent commits for the implementation.
7. Push only to the dedicated Syllune repository. The release workflow accepts `syllune-v*` tags and manual builds. A manual run builds an artifact; only a matching tag creates a public release. It never publishes to PyPI.

Checksums detect an incomplete or mismatched download; they do not replace code signing or independent provenance verification. Do not claim a signing identity unless the release was signed and verified.

## Windows signing and SmartScreen

The blue "Windows protected your PC" dialog is a download-reputation check. Trusted signing identifies the publisher and can carry publisher reputation across versions, but a new signed installer can still warn. Self-signed certificates do not establish public SmartScreen reputation. Microsoft's [SmartScreen guidance](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation) explains this limitation; [Store-distributed MSIX packages](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options) are re-signed by Microsoft and avoid SmartScreen download warnings. Store distribution requires a separate packaging/submission decision.

This build supports an **already-provisioned** trusted code-signing certificate with a private-key association in `Cert:\CurrentUser\My` or `Cert:\LocalMachine\My`, including a supported hardware-backed key exposed through the Windows certificate store. Provisioning, account creation, certificate purchases, and Store submission are outside the build. The key provider may require its normal PIN prompt. A valid chain on the build computer alone does not prove trust on every recipient's computer; public distribution requires an appropriate publicly trusted signing identity.

With Windows SDK SignTool already available, run:

```powershell
.\packaging\windows\build.ps1 -Signed -CertificateThumbprint 'YOUR_40_CHARACTER_CERTIFICATE_THUMBPRINT'
```

Use `-SignToolPath 'C:\...\signtool.exe'` if SignTool is outside `PATH` and the standard Windows SDK directories. The default RFC 3161 endpoint is `http://timestamp.digicert.com`; `-TimestampServer 'https://your-approved-timestamp-server'` selects another compatible endpoint. Timestamping requires network access. Offline signed builds are rejected.

Signed mode checks the certificate's validity, private-key association, code-signing usage, and chain before any expensive work. It signs the frozen Python engine **before** Electron packs it, then requires Electron to sign the desktop executable, installer, and uninstaller using SHA-256 and the selected timestamp service. It verifies the requested signer, trusted signature, and timestamp on the final installer, unpacked app, and packaged engine before generating the checksum. SignTool warnings are failures, including a missing timestamp; there is no unsigned fallback. See Microsoft's [SignTool reference](https://learn.microsoft.com/windows/win32/seccrypto/signtool).

The default build and existing release workflow remain unsigned unless explicitly configured for signed mode. No signing identity is shipped with this source. Without a trusted identity, a signed production release is blocked; an unsigned debug artifact must be described as unsigned. A successful signed build is evidence of signature verification, not proof that SmartScreen will suppress its warning for a fresh public download.
