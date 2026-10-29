"""One mountain valley painted at four times of day: peaks with snow, mist, pine forest, sky."""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1920, 1080
OUT = sys.argv[1]
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
Y, X = yy / H, xx / W


def hexc(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def mix(a, b, t):
    return a * (1 - t) + b * t


def over(img, colour, alpha):
    c = colour if isinstance(colour, np.ndarray) and colour.ndim == 3 else np.asarray(colour, np.float32)
    return img * (1 - alpha[..., None]) + c * alpha[..., None]


def sky(stops):
    img = np.zeros((H, W, 3), np.float32)
    pos = [p for p, _ in stops]
    cols = [hexc(c) for _, c in stops]
    for ch in range(3):
        img[..., ch] = np.interp(Y, pos, [c[ch] for c in cols])
    return img


def glow(img, cx, cy, r, colour, s, squash=1.0):
    d = np.sqrt(((X - cx) * W / H) ** 2 + ((Y - cy) / squash) ** 2)
    return over(img, hexc(colour), np.exp(-(d / r) ** 2) * s)


def fbm1d(seed, n, octaves, rough=0.55, ridged=False):
    rng = np.random.default_rng(seed)
    out = np.zeros(n, np.float32)
    amp, f = 1.0, 3
    for _ in range(octaves):
        pts = rng.random(f + 2) * 2 - 1
        xs = np.linspace(0, f + 1, n)
        i = xs.astype(int)
        t = xs - i
        t = t * t * (3 - 2 * t)
        v = pts[i] * (1 - t) + pts[np.minimum(i + 1, f + 1)] * t
        if ridged:
            v = 1 - np.abs(v) * 2
        out += v * amp
        amp *= rough
        f *= 2
    return out


def noise2d(seed, scale, octaves=4):
    rng = np.random.default_rng(seed)
    out = np.zeros((H, W), np.float32)
    amp = 1.0
    for o in range(octaves):
        gh, gw = max(2, int(H / scale * 2 ** o)), max(2, int(W / scale * 2 ** o))
        g = Image.fromarray((rng.random((gh, gw)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
        out += (np.asarray(g, np.float32) / 255 - 0.5) * amp
        amp *= 0.5
    return out


def mountains(seed, base, height, peak_x, peak_boost, octaves=8):
    """Ridge line (in 0..1 screen y) with one dominant peak."""
    r = fbm1d(seed, W, octaves, 0.47, ridged=True)
    r = (r - r.min()) / (r.max() - r.min())
    x = np.linspace(0, 1, W)
    bump = (np.exp(-np.abs(x - peak_x) / 0.07) * 0.7 + np.exp(-((x - peak_x) / 0.2) ** 2) * 0.3) * peak_boost
    return base - height * (r * 0.45 + bump)


def ridged2d(seed, sx, sy, octaves=6):
    """Sharp-crested 2D noise; sx, sy are feature sizes in pixels (sy > sx gives downhill gullies)."""
    rng = np.random.default_rng(seed)
    out = np.zeros((H, W), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        gh, gw = max(2, int(H / sy * 2 ** o)) + 1, max(2, int(W / sx * 2 ** o)) + 1
        g = Image.fromarray((rng.random((gh, gw)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
        n = np.asarray(g, np.float32) / 255 * 2 - 1
        out += (1 - np.abs(n)) ** 2 * amp
        tot += amp
        amp *= 0.5
    return out / tot


def blur1d(v, sigma):
    if sigma < 0.5:
        return v.copy()
    r = int(sigma * 3)
    k = np.exp(-(np.arange(-r, r + 1) / sigma) ** 2 / 2)
    k /= k.sum()
    pad = np.pad(v, r, mode="edge")
    return np.convolve(pad, k, mode="valid")


def paint_range(img, ridge, rock, lit, shadow, snow, snow_depth, light_dir, haze_col, haze, seed):
    top = ridge[None, :]
    inside = np.clip((Y - top) * H / 1.2, 0, 1)
    depth = np.clip(Y - top, 0, None)
    # The mountain body: the ridge spread wider the further down we look, so each peak
    # becomes a pyramid with faces falling left and right.  Its slope decides the light.
    elev = (ridge.max() - ridge) * H
    sigmas = [0, 6, 16, 34, 64, 110, 180, 280]
    stack = np.stack([blur1d(elev, sg) for sg in sigmas])           # (len, W)
    slope = np.gradient(stack, axis=1)                              # d elevation / dx
    dpx = depth * H
    pos = np.interp(dpx * 0.8, sigmas, np.arange(len(sigmas)))
    i0 = np.clip(np.floor(pos).astype(int), 0, len(sigmas) - 2)
    f = pos - i0
    cols = np.arange(W)[None, :].repeat(H, 0)
    sx = slope[i0, cols] * (1 - f) + slope[i0 + 1, cols] * f
    t = ridged2d(seed, 70, 120)
    ty, tx = np.gradient(t)
    gx = -sx * 1.6 + tx * H * 0.05      # surface tilt across the screen
    gy = 0.6 + ty * H * 0.05            # faces lean back away from us
    lx, ly, lz = 0.8 * light_dir, -0.2, 0.5
    shade = (gx * lx - gy * ly + lz) / np.sqrt(gx ** 2 + gy ** 2 + 1) / np.sqrt(lx ** 2 + ly ** 2 + lz ** 2)
    shade = (shade - 0.25) * 1.6
    k = np.clip(shade, -1, 1)[..., None]
    base = np.ones((H, W, 1)) * rock
    col = np.where(k > 0, mix(base, lit, k), mix(base, shadow, -k))
    # snow down from the summits, deeper on gentle ground, ragged along gullies
    peak_height = np.clip(elev / (elev.max() + 1e-6), 0, 1)[None, :]
    sd = snow_depth * peak_height ** 1.3 * (0.7 + 0.6 * (t - 0.4))
    snowy = np.clip((sd - depth) / 0.012, 0, 1) * np.clip((peak_height - 0.25) * 4, 0, 1)
    snow_col = mix(mix(snow, shadow, 0.55), snow, np.clip(k * 0.6 + 0.55, 0, 1))
    col = mix(col, snow_col, snowy[..., None])
    col = mix(col, haze_col, haze + 0.45 * np.clip(depth / 0.3, 0, 1)[..., None])
    return over(img, col, inside)


def mist(img, y, thick, colour, s, seed):
    n = noise2d(seed, 220, 4)
    band = np.exp(-((Y - y - n * 0.05) / thick) ** 2) * np.clip(0.6 + n * 1.4, 0, 1)
    return over(img, hexc(colour), band * s)


def forest(img, seed, base, tree_h, density, colour, edge_colour=None, spread=0.04):
    rng = np.random.default_rng(seed)
    line = fbm1d(seed + 7, W, 4, 0.5) * spread
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    x = -20
    while x < W + 20:
        gy = (base + line[min(max(int(x), 0), W - 1)]) * H
        h = tree_h * H * (0.6 + 0.8 * rng.random())
        w = h * (0.26 + 0.08 * rng.random())
        tiers = 6
        for t in range(tiers):
            ty = gy - h * t / tiers * 0.85
            tw = w * (1 - t / tiers) * 0.95 + w * 0.08
            d.polygon([(x - tw / 2, ty), (x + tw / 2, ty), (x, ty - h / tiers * 1.9)], fill=255)
        d.rectangle([x - w * 0.04, gy - 2, x + w * 0.04, gy + 10], fill=255)
        x += w * (0.35 + rng.random() * 0.5) / density
    # solid ground under the trees
    ground = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(ground)
    pts = [(i, (base + line[i]) * H) for i in range(0, W, 8)] + [(W, H), (0, H)]
    gd.polygon(pts, fill=255)
    m = np.maximum(np.asarray(mask, np.float32), np.asarray(ground, np.float32)) / 255
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8)), np.float32) / 255
    return over(img, hexc(colour), m)


def stars(img, seed, n, top=0.65, band=None):
    rng = np.random.default_rng(seed)
    for _ in range(n):
        x, y = rng.random() * W, (rng.random() ** 1.3) * H * top
        if band is not None and rng.random() < 0.55:
            # crowd stars along the Milky Way
            t = rng.random()
            bx, by = band[0][0] + (band[1][0] - band[0][0]) * t, band[0][1] + (band[1][1] - band[0][1]) * t
            x, y = bx * W + rng.normal(0, 90), by * H + rng.normal(0, 60)
        x, y = int(x), int(y)
        if not (0 <= x < W and 0 <= y < H):
            continue
        b = 110 + 145 * rng.random() ** 4
        r = 1 if rng.random() < 0.9 else 2
        img[max(0, y - r + 1):y + r, max(0, x - r + 1):x + r] = np.maximum(img[max(0, y - r + 1):y + r, max(0, x - r + 1):x + r], b)
    return img


def milky_way(img, p0, p1, width):
    # distance to the band's centre line
    ax, ay = p0[0] * W, p0[1] * H
    bx, by = p1[0] * W, p1[1] * H
    vx, vy = bx - ax, by - ay
    L = np.hypot(vx, vy)
    dist = np.abs((xx - ax) * vy - (yy - ay) * vx) / L / H
    n = noise2d(77, 80, 6)
    core = np.exp(-(dist / width) ** 2) * np.clip(0.55 + n * 1.6, 0, 1)
    img = over(img, hexc("#B8A7E0"), core * 0.32)
    img = over(img, hexc("#F2D9C4"), np.exp(-(dist / (width * 0.35)) ** 2) * np.clip(0.4 + n * 2, 0, 1) * 0.22)
    dust = np.exp(-((dist - width * 0.1) / (width * 0.18)) ** 2) * np.clip(n * 3, 0, 1)
    return over(img, hexc("#0A0E22"), dust * 0.5)


def finish(img, name, bottom=0.5):
    v = np.clip((Y - 0.55) / 0.45, 0, 1) ** 1.2 * bottom
    img = img * (1 - v[..., None])
    corner = np.clip(1 - X / 0.4, 0, 1) * np.clip(1 - Y / 0.35, 0, 1) * 0.22
    img = img * (1 - corner[..., None])
    img += np.random.default_rng(1).normal(0, 1.8, img.shape)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(f"{OUT}/{name}.jpg", quality=86, optimize=True)


# the same valley for every time of day
FAR = mountains(5, 0.56, 0.36, 0.62, 0.55, 7)
MID = mountains(9, 0.63, 0.16, 0.25, 0.25, 6)


def scene(p):
    img = sky(p["sky"])
    for g in p.get("glows", []):
        img = glow(img, *g)
    if p.get("night"):
        img = milky_way(img, (0.05, 0.05), (0.95, 0.55), 0.09)
        img = stars(img, 3, 1400, band=((0.05, 0.05), (0.95, 0.55)))
    if "sun" in p:
        cx, cy, r, c = p["sun"]
        d = np.sqrt(((X - cx) * W / H) ** 2 + (Y - cy) ** 2)
        img = over(img, hexc(c), np.clip((r - d) * H / 2, 0, 1))
    img = paint_range(img, FAR, hexc(p["rock"]), hexc(p["lit"]), hexc(p["shadow"]), hexc(p["snow"]),
                      0.055, p["light"], hexc(p["haze"]), 0.05, 21)
    img = mist(img, 0.6, 0.035, p["mist"], p["mist_s"], 31)
    img = paint_range(img, MID, hexc(p["rock2"]), hexc(p["lit2"]), hexc(p["shadow2"]), hexc(p["snow"]),
                      0.0, p["light"], hexc(p["haze"]), 0.15, 22)
    img = forest(img, 41, 0.7, 0.07, 1.6, p["trees_far"])
    img = mist(img, 0.69, 0.025, p["mist"], p["mist_s"] * 0.8, 32)
    img = forest(img, 51, 0.84, 0.16, 1.1, p["trees_near"], spread=0.06)
    finish(img, p["name"], p.get("bottom", 0.5))


scene(dict(name="morning",
           sky=[(0, "#2E3E6E"), (0.3, "#7A7BB0"), (0.5, "#E7A6A0"), (0.62, "#F8CFA0"), (1, "#F8CFA0")],
           glows=[(0.8, 0.5, 0.35, "#FFE2B0", 0.75)], sun=(0.8, 0.5, 0.03, "#FFF4DE"),
           rock="#74597A", lit="#E3A48C", shadow="#3E3460", snow="#FFE6D6", light=1,
           rock2="#5E4C6E", lit2="#A87E8A", shadow2="#3A3058", haze="#E9B5A8",
           mist="#F6D6C8", mist_s=0.55, trees_far="#2E2A44", trees_near="#141223"))

scene(dict(name="day",
           sky=[(0, "#1A4E8C"), (0.3, "#3C83C4"), (0.55, "#A6CCE6"), (1, "#C4DCEB")],
           glows=[(0.85, 0.05, 0.4, "#E6F2FA", 0.35)],
           rock="#5E6E82", lit="#A9B6C4", shadow="#2E3A52", snow="#F7FAFD", light=-1,
           rock2="#4A6878", lit2="#7E9DAA", shadow2="#2E4655", haze="#B9D3E4",
           mist="#E8F1F7", mist_s=0.5, trees_far="#1F3D36", trees_near="#0C1D19", bottom=0.55))

scene(dict(name="evening",
           sky=[(0, "#1C1745"), (0.25, "#4D3480"), (0.42, "#B5508A"), (0.55, "#F2885A"), (1, "#F7A860")],
           glows=[(0.22, 0.52, 0.32, "#FFB46A", 0.7)], sun=(0.22, 0.53, 0.032, "#FFE0B0"),
           rock="#5A3462", lit="#D9846E", shadow="#2E1E46", snow="#FFD0B8", light=-1,
           rock2="#4A2C5A", lit2="#8E5A78", shadow2="#2C1C40", haze="#E08A88",
           mist="#E8A0A8", mist_s=0.45, trees_far="#22172E", trees_near="#0E0915"))

scene(dict(name="night",
           sky=[(0, "#04061A"), (0.4, "#0B1638"), (0.62, "#182A58"), (1, "#182A58")],
           night=True,
           rock="#18223E", lit="#36476E", shadow="#0A1128", snow="#9EAFD4", light=1,
           rock2="#141E3A", lit2="#26365E", shadow2="#0A1028", haze="#1A2A52",
           mist="#3A4E7E", mist_s=0.35, trees_far="#0A1024", trees_near="#04060F", bottom=0.35))
