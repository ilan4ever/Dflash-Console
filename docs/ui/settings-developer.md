# Developer diagnostics

There is no separate Developer settings panel. Developer-facing diagnostics
are available in the **Engines** view:

- Engine lifecycle, model activity, inference, warning, and error log filters
- Copy, refresh, clear, hide, and resize controls for engine logs
- Live token count, speed, generation timer, and runtime inspector
- Direct API documentation under **Documentation**

The backend can also be run in foreground mode with `.\server.ps1 -Foreground`
when terminal logs are needed. Runtime limits and launch presets are configured
under Settings rather than a hidden developer-mode switch.
