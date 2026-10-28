# Reproducible raster companions for the editable Syllune vector mark.
# Supersampling keeps the same five rounded sound bars legible in the tray.
# This creates original assets; it never reads or modifies user images.

from pathlib import Path
from PIL import Image, ImageDraw

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


if __name__ == '__main__':
    folder = ROOT / 'overlay' / 'assets'
    folder.mkdir(parents=True, exist_ok=True)
    image = create_icon()
    image.save(folder / 'syllune.png')
    image.save(folder / 'syllune.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    tray = ROOT / 'src' / 'whisper_key' / 'assets' / 'brand'
    tray.mkdir(parents=True, exist_ok=True)
    image.resize((64, 64), Image.Resampling.LANCZOS).save(tray / 'idle.png')
    for name, color in [('recording', '#F18D9E'), ('processing', '#86ACD8')]:
        state = image.copy()
        ImageDraw.Draw(state).ellipse((740, 740, 966, 966), fill=color, outline='#101929', width=25)
        state.resize((64, 64), Image.Resampling.LANCZOS).save(tray / f'{name}.png')
