# Syllune on Microsoft Store

The intended simple install is a **Store-distributed MSIX**. Microsoft signs that package, so the Store installation avoids SmartScreen download warnings. Uploading the existing EXE to the Store is a different route and still requires the publisher to sign the EXE. See Microsoft's [signing guidance](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options).

Local packaging support is prepared in `packaging/windows/store.ps1`. It copies a completed x64 Electron payload into a new, isolated directory, adds the existing Syllune mark at the required sizes, declares the full-trust desktop entry point and microphone capability, and runs SDK MakeAppx with validation enabled. It never installs, signs, creates certificates, changes security settings, creates accounts, or uploads anything. Keep the NSIS build finished and idle while taking this snapshot.

## Local packaging smoke check

After `packaging/windows/build.ps1` finishes:

```powershell
.\packaging\windows\store.ps1 -ValidationOnly
```

The output is `.temp/store-<unique-id>/Syllune-VALIDATION-ONLY-x64.msix` plus staging, checksum, and status. Its deliberately separate unsigned development identity **must never be submitted or described as a Store release**. No installation or runtime behavior is proved by this check. Do not change Windows trust, enable developer mode, or install an unsigned package merely to complete the smoke check.

## Before a Store candidate can be finalized

The account owner must complete Microsoft's [developer verification](https://learn.microsoft.com/windows/apps/publish/partner-center/open-a-developer-account), reserve the app name, and obtain these exact [identity values](https://learn.microsoft.com/windows/apps/publish/view-app-identity-details) from Partner Center: `Package/Identity/Name`, `Package/Identity/Publisher`, and `Package/Properties/PublisherDisplayName`. The new onboarding flow currently has no registration fee, but account type must match the publisher's actual situation and identity/business verification remains necessary. Account creation, identity documents, agreements, name reservation, submission, and public release require the owner's action or explicit authorization.

Once those values exist, prepare a local candidate with them:

```powershell
.\packaging\windows\store.ps1 `
  -IdentityName '<exact Partner Center name>' `
  -Publisher '<exact Partner Center publisher distinguished name>' `
  -PublisherDisplayName '<exact Partner Center publisher display name>' `
  -PackageVersion '<approved four-part Store version>'
```

The script checks syntax; it cannot verify account ownership or that supplied values match Partner Center. An explicit Store version is required: each part is 0–65535, the first must be nonzero, and the fourth must be 0. App version `0.24.1` therefore cannot be copied directly to `0.24.1.0`; choose and record an increasing Store version separately, without silently changing the app's release version. See [package version requirements](https://learn.microsoft.com/windows/apps/publish/publish-your-app/msix/app-package-requirements).

## Runtime work still required

This is packaging preparation, **not a Store-ready release**. The current app uses `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` for login startup. A packaged build needs package-aware startup-task integration and must launch Electron rather than an engine-only process. Do not claim the existing login-startup toggle works in an MSIX install.

Electron and Python both use `%APPDATA%\Syllune` and `%LOCALAPPDATA%\Syllune`. [MSIX virtualization](https://learn.microsoft.com/windows/msix/desktop/flexible-virtualization) can redirect newly created data and registry entries, with different uninstall behavior. A packaged test must establish the intended settings/history/model-cache locations, engine/UI agreement, migration from the NSIS install, and upgrade/uninstall behavior before release.

The packaged flow also needs a real install/launch test, microphone permission and capture, global hotkeys, clipboard/insertion into another app, model download/cache, meeting recording, login startup, updates, and an uninstall/data-retention review. Run Windows App Certification Kit and explain the `runFullTrust` restricted capability during certification. The Store listing still needs reviewed screenshots, privacy/support information, age ratings, and certification. None of those are proved by successful MakeAppx validation.
