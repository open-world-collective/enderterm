# EnderTerm app icons

Source (master):
- `enderterm-icon-1024.png`

Derived:
- macOS: `EnderTerm.icns` (generated from `EnderTerm.iconset/`)
- Windows: `EnderTerm.ico`
- Convenience PNG sizes: `enderterm-icon-{512,256,128,64,32,16}.png`

Regenerate (macOS):
```bash
python - <<'PY'
from pathlib import Path
from PIL import Image

img = Image.open("icons/enderterm-icon-1024.png").convert("RGBA")
iconset = Path("icons/EnderTerm.iconset")
iconset.mkdir(parents=True, exist_ok=True)

mapping = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
for name, s in mapping.items():
    img.resize((s, s), resample=Image.Resampling.LANCZOS).save(iconset / name)
img.save("icons/EnderTerm.ico", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
PY

iconutil -c icns icons/EnderTerm.iconset -o icons/EnderTerm.icns
```
