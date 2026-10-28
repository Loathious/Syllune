# Security policy

Syllune runs on your own computer and keeps your recordings there, so security reports matter. Thank you for sending one.

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately instead:

1. Go to <https://github.com/Loathious/Syllune/security/advisories/new>
2. Describe the problem, how to reproduce it, the Syllune version and what an attacker could do with it

You'll get a reply, and the fix is coordinated with you before anything is made public.

## What counts

- Audio, transcripts or settings leaving the computer without the user choosing it. The only network requests Syllune makes on its own are speech-model downloads and the optional update check; the AI edit talks to a local Ollama server.
- A dictated phrase that runs a command the user didn't set up.
- Clipboard contents leaking between apps during automatic insertion.
- Anything that needs or gains administrator rights. Syllune never should.
- A vulnerability in a bundled component that affects Syllune itself.

Problems that need someone to edit your settings files themselves, or that only affect a component Syllune doesn't use, are out of scope.

## Supported versions

Only the latest release gets fixes. Please check that the problem still happens there.
