#!/usr/bin/env python3
"""A picture of how the bot's answers will look in Discord's dark theme, drawn here so the layout can be judged
before the bot has ever logged in.  It is a likeness, not Discord: fonts and spacing are close, not identical.

    python3 preview.py [--url http://your-server:8765] [command[=name] ...]      -> preview.png
"""
import os, re, sys, time
from PIL import Image, ImageDraw, ImageFont
import satmap_data as data

HERE = os.path.dirname(os.path.abspath(__file__))
K = 2                                             # drawn at twice the size, for smooth edges
BG, EMBED, CODE, TEXT, MUTED, WHITE, LINK = (49, 51, 56), (43, 45, 49), (30, 31, 34), (219, 222, 225), (148, 155, 164), (242, 243, 245), (0, 168, 252)
DOTS = {'🟢': (35, 165, 90), '🟡': (253, 203, 88), '🟠': (244, 144, 12), '🔴': (221, 46, 68), '⚪': (230, 231, 232)}
def F(size, kind='Regular'):
    path = {'mono': '/usr/share/fonts/liberation/LiberationMono-Regular.ttf', 'sym': '/usr/share/fonts/TTF/DejaVuSans.ttf'}.get(kind, '/usr/share/fonts/TTF/Roboto-%s.ttf' % kind)
    return ImageFont.truetype(path, size * K)

def tokens(line):
    """a line as (text, style) pieces: bold, code, a state dot, a bar, plain"""
    line = re.sub(r'<t:(\d+):R>', lambda m: data.ago(time.time() - int(m.group(1))) + ' ago', line).replace('⏸️', '⏸').replace('▫️', '▫').replace('🔋', '')
    out = []
    for part in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', line):
        if part.startswith('**'): out.append((part[2:-2], 'bold'))
        elif part.startswith('`'): out.append((part[1:-1], 'code'))
        else:
            for piece in re.split('([🟢🟡🟠🔴⚪]|[▰▱⏸▫]+)', part):
                if piece in DOTS: out.append((piece, 'dot'))
                elif piece and piece[0] in '▰▱⏸▫': out.append((piece, 'sym'))
                elif piece: out.append((piece, 'plain'))
    return out

class Pen:
    def __init__(self, d, x, y, width, size=14, colour=TEXT):
        self.d, self.x0, self.x, self.y, self.width, self.size, self.colour = d, x, x, y, width, size, colour
    def font(self, style):
        return F(self.size - 1, 'mono') if style == 'code' else F(self.size, 'sym') if style == 'sym' else F(self.size, 'Bold' if style == 'bold' else 'Regular')
    def newline(self): self.x = self.x0; self.y += int(self.size * 1.42) * K
    def write(self, line, colour=None):
        colour = colour or self.colour
        for text, style in tokens(line):
            if style == 'dot':
                r = int(self.size * 0.42) * K; cy = self.y + int(self.size * 0.62) * K
                self.d.ellipse((self.x + K, cy - r, self.x + K + 2 * r, cy + r), fill=DOTS[text]); self.x += 2 * r + 5 * K; continue
            font = self.font(style)
            for word in re.findall(r'\S+\s*|\s+', text):
                w = self.d.textlength(word, font=font)
                if self.x + w > self.x0 + self.width and self.x > self.x0: self.newline()
                if style == 'code':
                    self.d.rounded_rectangle((self.x - 2 * K, self.y, self.x + self.d.textlength(word.rstrip(), font=font) + 2 * K, self.y + int(self.size * 1.3) * K), radius=3 * K, fill=CODE)
                self.d.text((self.x, self.y), word, font=font, fill=WHITE if style == 'bold' else colour); self.x += w
        self.newline()

def block(pen, text):
    """markdown as the bot writes it: lines, ``` blocks, -# small print"""
    lines, i = text.split('\n'), 0
    while i < len(lines):
        if lines[i].startswith('```'):
            j = i + 1
            while j < len(lines) and not lines[j].startswith('```'): j += 1
            body = lines[i + 1:j]; font = F(pen.size - 2, 'mono'); step = int(pen.size * 1.25) * K
            pen.d.rounded_rectangle((pen.x0, pen.y + 2 * K, pen.x0 + pen.width, pen.y + step * len(body) + 14 * K), radius=4 * K, fill=CODE)
            for n, row in enumerate(body): pen.d.text((pen.x0 + 8 * K, pen.y + 8 * K + n * step), row, font=font, fill=TEXT)
            pen.y += step * len(body) + 20 * K; i = j + 1; continue
        if lines[i].startswith('-# '):
            small = Pen(pen.d, pen.x0, pen.y, pen.width, pen.size - 2, MUTED); small.write(lines[i][3:]); pen.y = small.y
        else:
            pen.write(lines[i])
        i += 1

def draw(card, button='Open the map'):
    W = 560 * K
    img = Image.new('RGB', (W + 90 * K, 2400 * K), BG); d = ImageDraw.Draw(img)
    face = Image.open(os.path.join(HERE, 'art', 'satmap-96.png')).convert('RGBA')
    mask = Image.new('L', (40 * K, 40 * K), 0); ImageDraw.Draw(mask).ellipse((0, 0, 40 * K - 1, 40 * K - 1), fill=255)
    img.paste(face.resize((40 * K, 40 * K), Image.LANCZOS), (16 * K, 14 * K), mask)
    d.text((70 * K, 14 * K), 'SatMap', font=F(15, 'Bold'), fill=(250, 149, 73))
    d.rounded_rectangle((128 * K, 16 * K, 160 * K, 31 * K), radius=3 * K, fill=(88, 101, 242)); d.text((133 * K, 18 * K), 'APP', font=F(9, 'Bold'), fill=WHITE)
    d.text((168 * K, 17 * K), 'Today at 3:41 PM', font=F(11), fill=MUTED)
    x0, y0 = 70 * K, 40 * K
    inner = x0 + 16 * K
    thumb = os.path.join(HERE, 'art', 'badges', card['badge'] + '.png')
    wide = W - 110 * K - 32 * K - 96 * K          # Discord keeps the whole text column left of the small picture
    y = y0 + 12 * K
    d.rounded_rectangle((x0, y0, x0 + W - 60 * K, 2300 * K), radius=4 * K, fill=EMBED)
    small = face.resize((24 * K, 24 * K), Image.LANCZOS); m2 = mask.resize((24 * K, 24 * K), Image.LANCZOS); img.paste(small, (inner, y), m2)
    d.text((inner + 32 * K, y + 4 * K), 'SatMap', font=F(13, 'Bold'), fill=WHITE); y += 34 * K
    pen = Pen(d, inner, y, wide, 16, WHITE); pen.write('**%s**' % card['title']); y = pen.y + 4 * K
    pen = Pen(d, inner, y, wide, 14)
    if os.path.isfile(thumb):
        t = Image.open(thumb).convert('RGBA').resize((80 * K, 80 * K), Image.LANCZOS); img.paste(t, (x0 + W - 60 * K - 96 * K, y0 + 14 * K), t)
    for n, line in enumerate(card['lines']):
        block(pen, line)
    y = pen.y + 6 * K
    row, col_w = [], (wide - 16 * K) // 3
    def flush(row, y):
        tall = y
        for n, (name, value) in enumerate(row):
            p = Pen(d, inner + n * (col_w + 8 * K), y, col_w, 13, WHITE); p.write('**%s**' % name)
            p = Pen(d, inner + n * (col_w + 8 * K), p.y, col_w, 13); block(p, value); tall = max(tall, p.y)
        return tall + 8 * K
    for name, value, inline in card['fields']:
        if inline:
            row.append((name, value))
            if len(row) == 3: y = flush(row, y); row = []
        else:
            if row: y = flush(row, y); row = []
            p = Pen(d, inner, y, wide, 13, WHITE); p.write('**%s**' % name); p = Pen(d, inner, p.y, wide, 13); block(p, value); y = p.y + 8 * K
    if row: y = flush(row, y)
    if card['footer']:
        p = Pen(d, inner, y + 2 * K, wide, 11, MUTED); p.write(card['footer'] + '  •  Today at 3:41 PM'); y = p.y
    bottom = y + 10 * K
    d.rectangle((x0, bottom, x0 + W, 2400 * K), fill=BG)
    d.rounded_rectangle((x0, y0, x0 + 4 * K, bottom), radius=2 * K, fill=tuple(card['colour'].to_bytes(3, 'big')))
    d.rectangle((x0 + W - 60 * K, y0, x0 + W + 20 * K, bottom + 4 * K), fill=BG)
    if button:
        d.rounded_rectangle((x0, bottom + 8 * K, x0 + 118 * K, bottom + 40 * K), radius=4 * K, fill=(78, 80, 88)); d.text((x0 + 14 * K, bottom + 16 * K), button, font=F(13, 'Bold'), fill=WHITE)
        bottom += 44 * K
    return img.crop((0, 0, W + 30 * K, bottom + 14 * K))

def main():
    args = sys.argv[1:]
    url = 'http://127.0.0.1:8765'
    if '--url' in args: url = args[args.index('--url') + 1]; del args[args.index('--url'):args.index('--url') + 2]
    wanted = args or ['status', 'alerts', 'machines', 'stock', 'stock=Crushed Iron', 'rates', 'nodes=Coal', 'logistics', 'power', 'harddrives', 'elevator', 'help']
    sm = data.SatMap(url)
    shots = [draw(data.answer(sm, w.split('=')[0], w.split('=')[1] if '=' in w else None)) for w in wanted]
    cols = 3 if len(shots) > 4 else len(shots)
    width = max(s.width for s in shots)
    heights = [0] * cols
    place = []
    for s in shots:
        c = heights.index(min(heights)); place.append((c * width, heights[c])); heights[c] += s.height
    sheet = Image.new('RGB', (cols * width, max(heights)), BG)
    for s, at in zip(shots, place): sheet.paste(s, at)
    sheet = sheet.resize((sheet.width // K, sheet.height // K), Image.LANCZOS)
    sheet.save(os.path.join(HERE, 'preview.png'), optimize=True)
    print('preview.png: %d answers, %d x %d' % (len(shots), sheet.width, sheet.height))

if __name__ == '__main__':
    main()
