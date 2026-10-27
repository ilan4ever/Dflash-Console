# DFlash Console notices

Copyright © 2026 ILAN AVIV.

DFlash Console source code is licensed under the
[GNU Affero General Public License, version 3 or later](./LICENSE).
The AGPL license governs only material for which this project has the
necessary copyright permissions. It does not relicense third-party software,
runtime engines, native binaries, model weights, voices, documentation, or
other separately distributed assets.

## Third-party software and assets

Before redistributing a build, review the licenses and notices for:

- Electron, Electron Builder, and their transitive dependencies.
- The standalone 7-Zip extractor (`7za.exe`, © Igor Pavlov, GNU LGPL with the
  unRAR restriction) embedded in the Windows setup UI so installers can unpack
  their own package without requiring 7-Zip on the user's machine. See
  <https://www.7-zip.org/license.txt>.
- `static/vendor/` libraries and their bundled notices.
- `llama-server`, DFlash, Piper, whisper.cpp, CUDA, and other runtime
  binaries or libraries.
- Hugging Face repositories, model weights, tokenizers, voices, and
  project-provided metadata.
- Logos, fonts, icons, and other artwork that are not original DFlash Console
  material.

The Console can discover files already installed on a user's machine. A scan
or download feature does not grant permission to redistribute those files.
Model and runtime users are responsible for following the applicable
upstream licenses and terms.

The DFlash Console name and logos are trademarks or protected branding of
ILAN AVIV where applicable. They are governed separately by
[TRADEMARKS.md](./TRADEMARKS.md).
