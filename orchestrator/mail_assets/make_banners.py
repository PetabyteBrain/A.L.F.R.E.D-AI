"""Email banners (1200x300 PNG, shown at 600x150) for orchestrator/mail_templates.py.

    python orchestrator/mail_assets/make_banners.py --name "Acme" --tagline "Custom web solutions" --monogram AC

writes company-banner.png next to this file (git-ignored: it carries the real company's name) and
alfred-banner.png. Uses fonts that come with Windows (Engravers MT, Garamond, Segoe UI); pass --fonts for
another folder with the same files. Needs Pillow (not part of the server image).
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True, help="Company name")
ap.add_argument("--tagline", default="")
ap.add_argument("--monogram", default="", help="One or two letters (default: the name's first letter)")
ap.add_argument("--place", default="SWITZERLAND", help="Small text on the right ('' for none)")
ap.add_argument("--fonts", default="C:/Windows/Fonts")
ap.add_argument("--example", action="store_true", help="Write the neutral company-banner.example.png instead")
args = ap.parse_args()
F = Path(args.fonts)
OUT = Path(__file__).resolve().parent
AVATAR = ROOT / "dashboard" / "web" / "src" / "assets" / "avatar" / "greeting.webp"
W, H = 1200, 300


def font(name, size):
    return ImageFont.truetype(str(F / name), size)


def tracked(draw, xy, text, fnt, fill, tracking=0.0, anchor_right=False):
    """Draw text with letter spacing (tracking in em)."""
    x, y = xy
    widths = [draw.textlength(c, font=fnt) for c in text]
    extra = fnt.size * tracking
    total = sum(widths) + extra * (len(text) - 1)
    if anchor_right:
        x -= total
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=fnt, fill=fill)
        x += w + extra
    return total


def vignette(img, inner, outer):
    """Soft radial light from the left-centre."""
    glow = Image.new("RGB", img.size, outer)
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((-300, -250, 760, 550), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(160))
    lit = Image.new("RGB", img.size, inner)
    return Image.composite(lit, glow, mask)


def company(name, tagline, monogram, path, place="SWITZERLAND"):
    ink, ivory, dim, line = (6, 6, 6), (244, 241, 234), (141, 138, 129), (58, 56, 52)
    img = vignette(Image.new("RGB", (W, H)), (20, 20, 19), ink)
    d = ImageDraw.Draw(img)
    # monogram: letters overlapping and offset, like the logo
    mono = font("ENGR.TTF", 118)
    if len(monogram) == 2:
        d.text((96, 70), monogram[0], font=mono, fill=ivory)
        d.text((96 + 78, 70 + 56), monogram[1], font=mono, fill=ivory)
    else:
        d.text((112, 92), monogram, font=mono, fill=ivory)
    # rule
    d.line((330, 82, 330, 218), fill=line, width=2)
    # wordmark + tagline
    word = font("ENGR.TTF", 62)
    tracked(d, (378, 96), name.upper(), word, ivory, tracking=0.20)
    d.line((382, 186, 470, 186), fill=(31, 122, 70), width=3)
    tag = font("segoeui.ttf", 22)
    tracked(d, (380, 204), tagline.upper(), tag, dim, tracking=0.30)
    small = font("segoeui.ttf", 17)
    tracked(d, (W - 70, 136), place, small, (95, 93, 86), tracking=0.42, anchor_right=True)
    img.save(path, optimize=True)


def alfred(path):
    bg = (12, 12, 14)
    img = Image.new("RGB", (W, H), bg)
    # portrait on the right, fading into the background
    av = Image.open(AVATAR).convert("RGB").resize((300, 300), Image.LANCZOS)
    slab = Image.new("RGB", (W, H), bg)
    slab.paste(av, (W - 300, 0))
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    x0 = W - 300
    for i in range(170):                              # the portrait's left edge fades into the background
        md.line((x0 + i, 0, x0 + i, H), fill=int(255 * (i / 170) ** 1.5))
    md.rectangle((x0 + 170, 0, W, H), fill=255)
    img = Image.composite(slab, img, mask)
    d = ImageDraw.Draw(img)
    silver, ivory, muted = (205, 207, 214), (251, 250, 246), (150, 150, 158)
    d.line((90, 74, 520, 74), fill=(74, 76, 84), width=1)
    d.text((86, 88), "Alfred", font=font("GARA.TTF", 104), fill=ivory)
    tracked(d, (92, 208), "YOUR BUTLER  ·  AT YOUR SERVICE", font("segoeui.ttf", 20), muted, tracking=0.32)
    d.line((90, 248, 520, 248), fill=(74, 76, 84), width=1)
    d.line((90, 252, 300, 252), fill=silver, width=1)
    img.save(path, optimize=True)


company(args.name, args.tagline, (args.monogram or args.name[:1]).upper()[:2],
        OUT / ("company-banner.example.png" if args.example else "company-banner.png"), place=args.place)
alfred(OUT / "alfred-banner.png")
print("written to", OUT)
