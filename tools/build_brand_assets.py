# Reproducible raster companions for the editable Syllune vector mark.
# Supersampling keeps the same five rounded sound bars legible in the tray.
# Also draws the installer's sidebar and header bitmaps from the same mark.
# This creates original assets; it never reads or modifies user images.

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def create_icon():
    image = Image.new('RGBA', (1024, 1024))
    mask = Image.new('L', image.size)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, 992, 992), radius=224, fill=255)
    for y in range(1024):
        t = y / 1023
        color = tuple(round(a + (b-a)*t) for a, b in zip((36, 59, 90), (16, 25, 41)))
        ImageDraw.Draw(image).line((0, y, 1024, y), fill=color)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((33, 33, 991, 991), radius=223, outline=(134, 172, 216, 65), width=2)
    bars = ((244, 472, 312, 672), (361, 342, 429, 702), (478, 282, 546, 742),
            (595, 322, 663, 682), (712, 352, 780, 552))
    for box in bars:
        draw.rounded_rectangle(box, radius=34, fill=(216, 226, 238, 255))
    return image


# Vertical navy gradient matching the app icon, used behind installer art.
def navy_gradient(size):
    image = Image.new('RGB', size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        draw.line((0, y, size[0], y), fill=tuple(round(a + (b-a)*t) for a, b in zip((30, 48, 74), (14, 21, 35))))
    return image


def segoe(size, bold=False):
    try:
        return ImageFont.truetype('seguisb.ttf' if bold else 'segoeui.ttf', size)
    except OSError:
        return ImageFont.load_default()


# NSIS welcome/finish sidebar (164x314) and page header (150x57); NSIS wants
# 24-bit BMPs. The text is the product name only, calm and centred.
def create_installer_art(icon, folder):
    sidebar = navy_gradient((164, 314))
    mark = icon.resize((72, 72), Image.Resampling.LANCZOS)
    sidebar.paste(mark, (46, 96), mark)
    draw = ImageDraw.Draw(sidebar)
    draw.text((82, 190), 'Syllune', font=segoe(22, bold=True), fill=(226, 234, 244), anchor='mm')
    draw.text((82, 216), 'Local dictation', font=segoe(12), fill=(150, 172, 200), anchor='mm')
    sidebar.save(folder / 'installerSidebar.bmp')
    sidebar.save(folder / 'uninstallerSidebar.bmp')
    header = Image.new('RGB', (150, 57), (255, 255, 255))
    small = icon.resize((36, 36), Image.Resampling.LANCZOS)
    header.paste(small, (104, 10), small)
    header.save(folder / 'installerHeader.bmp')


if __name__ == '__main__':
    folder = ROOT / 'overlay' / 'assets'
    folder.mkdir(parents=True, exist_ok=True)
    image = create_icon()
    image.save(folder / 'syllune.png')
    image.save(folder / 'syllune.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    create_installer_art(image, folder)
    tray = ROOT / 'src' / 'syllune' / 'assets' / 'brand'
    tray.mkdir(parents=True, exist_ok=True)
    image.resize((64, 64), Image.Resampling.LANCZOS).save(tray / 'idle.png')
    for name, color in [('recording', '#F18D9E'), ('processing', '#86ACD8')]:
        state = image.copy()
        ImageDraw.Draw(state).ellipse((740, 740, 966, 966), fill=color, outline='#101929', width=25)
        state.resize((64, 64), Image.Resampling.LANCZOS).save(tray / f'{name}.png')
