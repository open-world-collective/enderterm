# macOS app build (beta)

This is a minimal build pipeline for producing a double-clickable `EnderTerm.app`
from the current repo using PyInstaller.

## Build

```bash
PY=~/tmp/venv/worker3/bin/python packaging/macos/build_app.sh
open ~/tmp/enderterm-pyinstaller/dist/EnderTerm.app
```

## Signing / notarization (for external testers)

Not included in `build_app.sh` yet, but this is the typical flow:

1) Codesign the app bundle with a Developer ID Application certificate
2) Zip or DMG it
3) Notarize the archive with `notarytool`
4) Staple the ticket

If you want, we can add a `sign_and_notarize.sh` that reads:
- bundle id (e.g. `com.yourcompany.enderterm`)
- signing identity
- `notarytool` profile name (Keychain) or API key env vars
