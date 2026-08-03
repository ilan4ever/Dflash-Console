# Security Policy

## Supported versions

Only the latest public release and the `main` branch receive security fixes.
Older packaged artifacts are unsupported. The release shown on the in-app
About page and in `package.json` is the authoritative current version.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or a private security
advisory for this repository. Do not open a public issue containing exploit
details, credentials, model URLs with embedded tokens, or private logs.

Include the affected version, operating system, reproduction steps, impact,
and any suggested mitigation. The maintainers will acknowledge reports as
soon as practical and will coordinate disclosure after a fix is available. The
project is maintained by **ILAN AVIV**.

## Security boundary

DFlash Console is a local, single-user application. It binds its Console and
engine APIs to loopback by default and rejects non-loopback engine URLs in
configuration. It does not provide authentication or CSRF protection for
deployment behind a LAN or public reverse proxy. Add an authenticated access
layer before changing the network boundary.

Keep `config.json`, Hugging Face tokens, model files, logs, crash dumps, and
packaging certificates outside version control. Verify third-party binaries
and model files before use.
