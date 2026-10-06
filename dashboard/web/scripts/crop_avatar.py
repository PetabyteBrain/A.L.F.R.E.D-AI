"""Cut the Alfred avatar sheet (docs/assets/alfred-avatar-sheet.jpg, 4 x 4 portraits on white gutters)
into one WebP per state in src/assets/avatar/. Run after replacing the sheet:
    .venv/Scripts/python dashboard/web/scripts/crop_avatar.py   (needs Pillow, dev only)
The state names are used by src/components/AlfredAvatar.jsx."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SHEET = ROOT / "docs" / "assets" / "alfred-avatar-sheet.jpg"
OUT = ROOT / "dashboard" / "web" / "src" / "assets" / "avatar"
STATES = [  # row by row, left to right
    "attentive", "greeting", "thinking", "lookout",
    "explaining", "reading", "firm", "idle",
    "pointing", "bow", "working", "listening",
    "pleased", "concerned", "tea", "neutral",
]
SIZE = 320


def runs(flags, min_len=100):
    out, start = [], None
    for i, f in enumerate(flags + [True]):
        if not f and start is None:
            start = i
        elif f and start is not None:
            if i - start > min_len:
                out.append((start, i))
            start = None
    return out


def main() -> None:
    im = Image.open(SHEET).convert("RGB")
    w, h = im.size
    px = im.load()
    white = lambda vals: sum(1 for v in vals if min(v) > 225) > len(vals) * 0.9  # noqa: E731
    cols = runs([white([px[x, y] for y in range(0, h, 3)]) for x in range(w)])
    rows = runs([white([px[x, y] for x in range(0, w, 3)]) for y in range(h)])
    assert len(cols) == 4 and len(rows) == 4, (cols, rows)
    OUT.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(STATES):
        (x0, x1), (y0, y1) = cols[i % 4], rows[i // 4]
        cell = im.crop((x0 + 2, y0 + 2, x1 - 2, y1 - 2)).resize((SIZE, SIZE), Image.LANCZOS)
        cell.save(OUT / f"{name}.webp", "WEBP", quality=84, method=6)
    print(f"{len(STATES)} portraits -> {OUT}")


if __name__ == "__main__":
    main()
