# Builds D:\ATC\FTS_1U_Servers.pptx: two white, clean 16:9 slides listing the
# 1U servers considered for colocation, each with a same-size product photo.
# Photos are letterboxed into identical tiles with PIL first (<assets>/tiles).
#
#   pip install python-pptx pillow
#   python scripts/decks/make_1u_slides.py [assets_dir] [out.pptx]
#
# assets_dir (default D:\ATC\deck_assets) holds srv_img/<vendor photos>; the
# photos are vendor marketing images and stay outside the repo. Export to
# PNG for checking with scripts/decks/export_slides.ps1 (PowerPoint COM).
# See DESKTOP_RELEASE_PLAN.md section 20 for where each photo came from.
import os, sys
from PIL import Image, ImageOps
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

ASSETS = sys.argv[1] if len(sys.argv) > 1 else r"D:\ATC\deck_assets"
IMG = os.path.join(ASSETS, "srv_img")
TILES = os.path.join(ASSETS, "tiles")
OUT = sys.argv[2] if len(sys.argv) > 2 else r"D:\ATC\FTS_1U_Servers.pptx"
os.makedirs(TILES, exist_ok=True)

FONT = "Arial"
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x6B, 0x72, 0x80)
RULE = RGBColor(0xD1, 0xD5, 0xDB)
ACCENT = RGBColor(0x0B, 0x72, 0x85)
TINT = RGBColor(0xF3, 0xF4, 0xF6)

TILE_W, TILE_H = 900, 420  # px, 15:7 like the 1.5 x 0.7 in box


PRECROP = {"gigabyte.jpg": (0, 0, 1, 0.88)}  # drop the vendor logo under the Gigabyte photo


def tile(src, name):
    """Crop white/transparent margins, then fit into one fixed white tile."""
    out = os.path.join(TILES, name + ".png")
    im = Image.open(os.path.join(IMG, src)).convert("RGBA")
    if src in PRECROP:
        l, t, r, b = PRECROP[src]
        im = im.crop((int(l * im.width), int(t * im.height), int(r * im.width), int(b * im.height)))
    white = Image.new("RGBA", im.size, (255, 255, 255, 255))
    white.alpha_composite(im)
    im = white.convert("RGB")
    # bounding box of pixels that are not near-white
    gray = ImageOps.invert(im.convert("L")).point(lambda v: 255 if v > 18 else 0)
    box = gray.getbbox()
    if box:
        pad = int(0.02 * max(im.size))
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
        im = im.crop(box)
    fitted = ImageOps.contain(im, (TILE_W - 40, TILE_H - 30), Image.LANCZOS)
    canvas = Image.new("RGB", (TILE_W, TILE_H), (255, 255, 255))
    canvas.paste(fitted, ((TILE_W - fitted.width) // 2, (TILE_H - fitted.height) // 2))
    canvas.save(out)
    return out


ROWS = [
    # (section, photo file or None, server, cpu, time, remote, price, notes)
    ("Tier 1 - fastest for the money (AM5 socket, 16 cores)", None, None, None, None, None, None, None),
    (None, "supermicro.png", "Supermicro AS-1015A-MT", "AMD EPYC 4545P, 16 cores / 32 threads, 65 W", "6 s / 17 s", "BMC, remote console included", "THB 95,000 - 115,000", "Short-depth 1U, 2 x 10 GbE. Distributor warranty (send-in), no on-site visit."),
    (None, "asrock.jpg", "ASRock Rack 1U4LW-B650/2L2T", "EPYC 4545P or Ryzen 9 9950X, 16 cores", "6 s / 17 s", "IPMI, remote console included", "THB 85,000 - 105,000", "Cheapest 16-core 1U. Same parts as the self-build, pre-assembled."),
    (None, "gigabyte.jpg", "Gigabyte R113-C10", "EPYC 4545P / 4584PX, 16 cores", "6 s / 17 s", "BMC included", "THB 90,000 - 110,000", "Similar to the Supermicro. Check Thai stock."),
    (None, "asus_rs300.jpg", "ASUS RS300-E12-PS4", "Intel Xeon E-2478, 8 cores / 16 threads", "12 s / 38 s", "ASMB iKVM included", "THB 85,000 - 105,000", "Cheapest brand-name Intel 1U. Remote console included, unlike Dell / HPE."),
    ("Tier 2 - brand-name Xeon E-2400 (8 cores, full on-site support)", None, None, None, None, None, None, None),
    (None, "dell_r260.png", "Dell PowerEdge R260", "Xeon E-2478 or E-2488, 8 cores", "12 s / 38 s", "iDRAC9 Enterprise, about +THB 10,000", "THB 100,000 - 130,000", "Single PSU. Max 128 GB (4 x 32 GB). Verified: 4-core base at THB 62,800 + VAT."),
    (None, "dell_r360.png", "Dell PowerEdge R360", "Xeon E-2478 or E-2488, 8 cores", "12 s / 38 s", "iDRAC9 Enterprise, about +THB 10,000", "THB 100,000 - 130,000", "Same CPUs as the R260 with redundant PSUs. Often the same price on quote."),
    # ---- slide 2 ----
    ("Tier 2 (continued)", None, None, None, None, None, None, None),
    (None, "hpe_dl20.jpg", "HPE ProLiant DL20 Gen11", "Xeon E-2478, 8 cores", "12 s / 38 s", "iLO Advanced, about +THB 12,000", "THB 110,000 - 140,000", "HPE's twin of the R260. Strong support network in Thailand."),
    (None, "LENOVO", "Lenovo ThinkSystem SR250 V3", "Xeon E-2478, 8 cores", "12 s / 38 s", "XCC Enterprise, about +THB 8,000", "THB 100,000 - 130,000", "Often the cheapest of the three big brands on quote."),
    (None, "FUJITSU", "Fujitsu PRIMERGY RX1330 M6", "Xeon E-2478, 8 cores", "12 s / 38 s", "iRMC Advanced, extra", "THB 105,000 - 135,000", "Fine, but less common in Thailand."),
    ("Tier 3 - more than we need (listed so the gap is visible)", None, None, None, None, None, None, None),
    (None, "TIER3A", "Dell R6615 / HPE DL325 Gen11 / Lenovo SR635 V3", "AMD EPYC 9124, 16 cores, 3.0 GHz", "7 s / 22 s", "Included in this class", "THB 220,000 - 300,000", "Slower per core than Tier 1 at 2.5x the price. 12 memory channels and 128 PCIe lanes go unused."),
    (None, "TIER3B", "Dell R470 / HPE DL320 Gen12", "Intel Xeon 6, 16 - 32 cores", "8 s / 25 s", "Extra licence", "THB 200,000 +", "Same story: pays for capacity this engine never touches."),
]
SLIDE_SPLIT = 8  # index where slide 2 starts

COLS = [  # (title, width in)
    ("Photo", 1.55), ("Server", 2.05), ("CPU", 1.85), ("1,000 flights\n1 user / 3 users", 1.25),
    ("Remote console", 1.55), ("Est. price, 64 GB,\nincl. VAT", 1.55), ("Notes", 2.53),
]
LEFT, TOP = 0.5, 1.1
ROW_H, SEC_H, HEAD_H = 0.73, 0.30, 0.46
PHOTO_W, PHOTO_H = 1.45, 0.68

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
blank = prs.slide_layouts[6]


def text(slide, x, y, w, h, s, size=10, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    first = True
    for line in s.split("\n"):
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        r = p.add_run()
        r.text = line
        r.font.name, r.font.size, r.font.bold = FONT, Pt(size), bold
        r.font.color.rgb = color
    return tb


def rule(slide, x, y, w, color=RULE, weight=0.75):
    ln = slide.shapes.add_connector(1, Inches(x), Inches(y), Inches(x + w), Inches(y))
    ln.line.color.rgb = color
    ln.line.width = Pt(weight)
    ln.shadow.inherit = False


def placeholder(slide, x, y, w, h, label):
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    box.adjustments[0] = 0.08
    box.fill.solid(); box.fill.fore_color.rgb = TINT
    box.line.color.rgb = RULE; box.line.width = Pt(0.75)
    box.shadow.inherit = False
    tf = box.text_frame
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = label
    r.font.name, r.font.size, r.font.color.rgb = FONT, Pt(8), MUTED


def header(slide, title, sub, page):
    text(slide, LEFT, 0.32, 11.0, 0.5, title, size=22, bold=True, anchor=MSO_ANCHOR.TOP)
    text(slide, LEFT, 0.78, 11.5, 0.35, sub, size=10.5, color=MUTED, anchor=MSO_ANCHOR.TOP)
    text(slide, 12.2, 0.35, 0.65, 0.3, f"{page} / 2", size=9, color=MUTED, align=PP_ALIGN.RIGHT)
    # column headings
    x = LEFT
    for title_, w in COLS:
        text(slide, x, TOP, w, HEAD_H, title_, size=9, bold=True, color=MUTED, anchor=MSO_ANCHOR.BOTTOM)
        x += w
    rule(slide, LEFT, TOP + HEAD_H + 0.02, sum(w for _, w in COLS), color=INK, weight=1.0)
    return TOP + HEAD_H + 0.04


def table(slide, rows, y):
    width = sum(w for _, w in COLS)
    for sec, photo, server, cpu, t, remote, price, notes in rows:
        if sec:
            text(slide, LEFT, y + 0.03, width, SEC_H, sec, size=10, bold=True, color=ACCENT)
            y += SEC_H + 0.03
            continue
        x = LEFT
        # photo tile, identical size for every row
        px, py = x + (COLS[0][1] - PHOTO_W) / 2, y + (ROW_H - PHOTO_H) / 2
        if photo in PLACEHOLDERS:
            placeholder(slide, px, py, PHOTO_W, PHOTO_H, PLACEHOLDERS[photo])
        else:
            slide.shapes.add_picture(tile(photo, os.path.splitext(photo)[0]), Inches(px), Inches(py), Inches(PHOTO_W), Inches(PHOTO_H))
        x += COLS[0][1]
        cells = [(server, 10, True, INK), (cpu, 9, False, INK), (t, 10.5, True, ACCENT), (remote, 9, False, INK), (price, 10, True, INK), (notes, 8.5, False, MUTED)]
        for (s, size, bold, color), (_, w) in zip(cells, COLS[1:]):
            text(slide, x, y, w, ROW_H, s, size=size, bold=bold, color=color, align=PP_ALIGN.CENTER if s == t else PP_ALIGN.LEFT)
            x += w
        y += ROW_H
        rule(slide, LEFT, y, width)
    return y


PLACEHOLDERS = {
    "LENOVO": "Photo: lenovo.com\n(SR250 V3)",
    "FUJITSU": "Photo: fujitsu.com\n(RX1330 M6)",
    "TIER3A": "Photo: vendor sites\n(EPYC 1U class)",
    "TIER3B": "Photo: vendor sites\n(Xeon 6 1U class)",
}

SUB = ("Estimated configured price with 64 GB ECC RAM and 2 x NVMe, incl. VAT. "
       "Time = one 1,000-flight simulation with 1 user, and with 3 users running at once.")

s1 = prs.slides.add_slide(blank)
y = header(s1, "1U rack servers for the FTS engine", SUB, 1)
y = table(s1, ROWS[:SLIDE_SPLIT], y)
text(s1, LEFT, 6.85, 12.3, 0.35,
     "Tier 1 boxes finish a run in about half the time of any Xeon E-2400 server because they have 16 fast cores where the E-2400 family stops at 8. "
     "With 128 GB instead of 64 GB add about THB 20,000 - 25,000 to any row.", size=9, color=MUTED, anchor=MSO_ANCHOR.TOP)

s2 = prs.slides.add_slide(blank)
y = header(s2, "1U rack servers for the FTS engine", SUB, 2)
y = table(s2, ROWS[SLIDE_SPLIT:], y)
y += 0.08
text(s2, LEFT, y, 12.3, 0.3, "How to read it", size=11, bold=True, color=ACCENT, anchor=MSO_ANCHOR.TOP)
bullets = (
    "Speed per baht: Supermicro AS-1015A-MT or the ASRock Rack box with an EPYC 4545P. Half the wait of any Xeon E-2400 server, for less money; trade-off is send-in warranty instead of next-day on-site.\n"
    "Company-standard support: Dell R360 or Lenovo SR250 V3 with the E-2478. Slower, but the vendor fixes it on site. All four Tier 2 brands are the same speed; choose on quote price and support.\n"
    "Any Tier 2 box with the 2025 Xeon 6369P (8 cores, 5.5 GHz) is about 11 s / 34 s for +THB 5,000 - 10,000. Suggested quotes: AS-1015A-MT with EPYC 4545P, and SR250 V3 with E-2478."
)
tb = text(s2, LEFT, y + 0.26, 12.3, 0.85, bullets, size=8.5, color=INK, anchor=MSO_ANCHOR.TOP)
for p in tb.text_frame.paragraphs:
    p.space_after = Pt(2)
    r = p.runs[0]; r.text = "\u2022  " + r.text
text(s2, LEFT, 7.02, 12.3, 0.3,
     "All prices are estimates (Thai retail for parts, regional list prices for brand servers, Oct 2026). The one verified anchor is Dell's 4-core R260 at THB 62,800 + VAT. "
     "Supermicro, ASRock Rack and Gigabyte sell through distributors (Synnex, SiS, Ingram, Quickserv); exact-model stock needs a call.", size=8, color=MUTED, anchor=MSO_ANCHOR.TOP)

prs.save(OUT)
print("saved", OUT)
