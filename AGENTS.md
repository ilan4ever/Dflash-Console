# Project Instructions

## General
- Always inspect the existing code before modifying it.
- Do not rewrite working code unnecessarily.
- Keep the existing architecture unless I explicitly ask to change it.
- Explain important changes briefly after completing them.

## Coding
- Use TypeScript.
- Prefer clean, modular code.
- Reuse existing components and utilities.
- Do not introduce new dependencies unless necessary.

## Testing
- Run the appropriate tests after making changes.
- Run the build when relevant.
- Fix errors you introduce before finishing.

## Terminal
- You may run normal development commands automatically.
- Do not delete files or directories unless I explicitly request it.
- Do not reset Git changes without asking me.
- NEVER change a repository's visibility / privacy (public ↔ private), transfer ownership, or change access/team/collaborator settings without my explicit permission. This includes GitHub settings — always ask first.

## Communication
- If something is unclear, inspect the project first.
- Before making a major architectural change, ask me.

## Releases
- When the user asks to **publish a release**, ship **GitHub tag + Windows installer (CI) + PyPI** (`dflash-console`). See `.cursor/rules/release.mdc` and `docs/RELEASING.md`.
- Use `.\scripts\publish-release.ps1` for the full flow; never skip PyPI unless the user explicitly requests GitHub-only.