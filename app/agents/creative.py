"""Agent 3 - Creative: renders REAL ad assets (1:1 + 9:16 images and a 9:16 video) per country.

No stock footage and no external generation service: Pillow draws the cards (with correct Arabic
shaping + RTL) and ffmpeg turns three scenes into an MP4. Everything works offline.
"""
from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
from pathlib import Path

import arabic_reshaper
import httpx
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import geo
from ..models import Campaign, Product
from ..services.netguard import UnsafeURL, assert_public_url
from .base import BaseAgent
from .facts import facts_for

log = logging.getLogger("creative")

BG_TOP, BG_BOTTOM = (11, 16, 32), (27, 42, 74)
ACCENT, WHITE, INK = (255, 176, 32), (255, 255, 255), (16, 22, 40)
_reshaper = arabic_reshaper.ArabicReshaper()


def shape(text: str) -> str:
    return get_display(_reshaper.reshape(text))


# ------------------------------------------------------------------------- fonts
def _font_candidates(bold: bool) -> list[str]:
    here = Path(__file__).resolve().parents[2] / "assets" / "fonts"
    local = [str(p) for p in sorted(here.glob("*.ttf"))] if here.exists() else []
    if bold:
        sysf = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/tahomabd.ttf",
                "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
                "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"]
    else:
        sysf = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/tahoma.ttf",
                "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
                "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"]
    return local + sysf


_font_cache: dict[tuple[bool, int, str], ImageFont.FreeTypeFont] = {}


def get_font(size: int, bold: bool, override: str = "") -> ImageFont.FreeTypeFont:
    key = (bold, size, override)
    if key in _font_cache:
        return _font_cache[key]
    cands = ([override] if override else []) + _font_candidates(bold)
    for path in cands:
        if path and Path(path).exists():
            try:
                f = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)  # we shape/bidi ourselves; raqm would double-process
                _font_cache[key] = f
                return f
            except OSError:
                continue
    log.warning("No Arabic-capable TTF found; set FONT_PATH or drop a .ttf into assets/fonts/")
    return ImageFont.load_default()


# ---------------------------------------------------------------------- drawing
def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(shape(text), font=font)


def wrap(draw, text: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if _text_w(draw, cand, font) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def draw_centered(draw, xy_center_x: int, y: int, text: str, font, fill, max_w: int, max_lines=3,
                  line_gap=1.25) -> int:
    lines = wrap(draw, text, font, max_w, max_lines)
    size = getattr(font, "size", 30)
    for ln in lines:
        w = _text_w(draw, ln, font)
        draw.text((xy_center_x - w / 2, y), shape(ln), font=font, fill=fill)
        y += int(size * line_gap)
    return y


def gradient(w: int, h: int) -> Image.Image:
    top = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        top.putpixel((0, y), tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    return top.resize((w, h))


def _glow(canvas: Image.Image, cx: int, cy: int, r: int):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT + (70,))
    layer = layer.filter(ImageFilter.GaussianBlur(r // 3))
    canvas.paste(layer, (0, 0), layer)


def _place_product(canvas: Image.Image, img: Image.Image | None, box: tuple[int, int, int, int],
                   fallback_label: str, font_override: str):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    _glow(canvas, cx, cy, min(bw, bh) // 2)
    if img is not None:
        im = img.convert("RGBA")
        im.thumbnail((bw, bh), Image.LANCZOS)
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 120))
        shadow.paste(sh, (cx - im.width // 2 + 12, cy - im.height // 2 + 18), im.split()[-1])
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        canvas.paste(shadow, (0, 0), shadow)
        canvas.paste(im, (cx - im.width // 2, cy - im.height // 2), im)
        return
    # no photo: draw a clean phone silhouette with the product name
    ph_h = int(bh * 0.95)
    ph_w = int(ph_h * 0.5)
    d = ImageDraw.Draw(canvas)
    px0, py0 = cx - ph_w // 2, cy - ph_h // 2
    d.rounded_rectangle((px0, py0, px0 + ph_w, py0 + ph_h), radius=ph_w // 7, fill=(20, 26, 46),
                        outline=(120, 135, 175), width=6)
    d.rounded_rectangle((px0 + 14, py0 + 14, px0 + ph_w - 14, py0 + ph_h - 14), radius=ph_w // 8,
                        fill=(33, 48, 88))
    f = get_font(max(ph_w // 8, 18), True, font_override)
    draw_centered(d, cx, cy - ph_h // 8, fallback_label, f, WHITE, ph_w - 60, 4)


def pill(draw, cx: int, y: int, text: str, font, bg, fg, pad_x=44, pad_y=22) -> int:
    w = _text_w(draw, text, font)
    size = getattr(font, "size", 30)
    x0, x1 = cx - w / 2 - pad_x, cx + w / 2 + pad_x
    y1 = y + size + pad_y * 2
    draw.rounded_rectangle((x0, y, x1, y1), radius=(y1 - y) // 2, fill=bg)
    draw.text((cx - w / 2, y + pad_y - size * 0.08), shape(text), font=font, fill=fg)
    return int(y1)


def render_card(w: int, h: int, *, headline: str, price: str | None, cta: str, specs: list[str],
                product_img: Image.Image | None, label: str, font_override: str, condition: str) -> Image.Image:
    canvas = gradient(w, h)
    portrait = h > w
    d = ImageDraw.Draw(canvas, "RGBA")
    pad = int(w * 0.07)
    f_head = get_font(int(w * (0.078 if portrait else 0.07)), True, font_override)
    f_spec = get_font(int(w * (0.044 if portrait else 0.036)), False, font_override)
    f_price = get_font(int(w * 0.075), True, font_override)
    f_cta = get_font(int(w * 0.05), True, font_override)
    f_tag = get_font(int(w * 0.034), True, font_override)

    y = int(h * 0.055)
    # condition tag
    y = pill(d, w // 2, y, condition, f_tag, (255, 255, 255, 30), ACCENT, 28, 10) + int(h * 0.02)
    y = draw_centered(d, w // 2, y, headline, f_head, WHITE, w - 2 * pad, 2 if not portrait else 3)

    prod_top = y + int(h * 0.01)
    prod_bottom = int(h * (0.62 if portrait else 0.60))
    _place_product(canvas, product_img, (pad, prod_top, w - pad, prod_bottom), label, font_override)
    d = ImageDraw.Draw(canvas, "RGBA")

    y = prod_bottom + int(h * 0.015)
    chips = specs[:3 if portrait else 2]
    if chips:
        y = draw_centered(d, w // 2, y, "  •  ".join(chips), f_spec, (210, 220, 240), w - 2 * pad, 2) + 6
    if price:
        y = pill(d, w // 2, y + int(h * 0.012), price, f_price, ACCENT, INK) + int(h * 0.018)
    # CTA bar
    cta_y = max(y, h - int(h * 0.05) - (f_cta.size + 36)) if portrait else max(y, h - int(h * 0.045) - (f_cta.size + 36))
    pill(d, w // 2, cta_y, cta, f_cta, WHITE, INK, 60, 18)
    return canvas


def render_scene(w: int, h: int, kind: str, *, headline: str, price: str | None, cta: str,
                 specs: list[str], product_img, label: str, font_override: str, condition: str) -> Image.Image:
    if kind == "hook":
        return render_card(w, h, headline=headline, price=None, cta=cta, specs=[], product_img=product_img,
                           label=label, font_override=font_override, condition=condition)
    canvas = gradient(w, h)
    d = ImageDraw.Draw(canvas, "RGBA")
    pad = int(w * 0.08)
    f_big = get_font(int(w * 0.085), True, font_override)
    f_mid = get_font(int(w * 0.06), True, font_override)
    f_price = get_font(int(w * 0.11), True, font_override)
    if kind == "specs":
        _place_product(canvas, product_img, (pad, int(h * 0.06), w - pad, int(h * 0.46)), label, font_override)
        d = ImageDraw.Draw(canvas, "RGBA")
        y = int(h * 0.53)
        for s in (specs or [headline])[:4]:
            y = pill(d, w // 2, y, s, f_mid, (255, 255, 255, 28), WHITE, 50, 24) + int(h * 0.025)
    else:  # offer
        _place_product(canvas, product_img, (pad, int(h * 0.08), w - pad, int(h * 0.42)), label, font_override)
        d = ImageDraw.Draw(canvas, "RGBA")
        y = int(h * 0.47)
        y = draw_centered(d, w // 2, y, headline, f_big, WHITE, w - 2 * pad, 2) + int(h * 0.02)
        if price:
            y = pill(d, w // 2, y, price, f_price, ACCENT, INK, 60, 30) + int(h * 0.04)
        pill(d, w // 2, y, cta, f_mid, WHITE, INK, 60, 24)
    return canvas


# ------------------------------------------------------------------------ agent
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


class CreativeAgent(BaseAgent):
    name = "creative"
    label = "وكيل الإبداع"

    async def run(self, db, campaign: Campaign, product: Product) -> list[dict]:
        out_dir = self.settings.media_dir / campaign.id
        out_dir.mkdir(parents=True, exist_ok=True)
        img = await self._load_product_image(product)
        if img is None:
            self.log(db, campaign.id, "لا توجد صورة للمنتج؛ سيتم رسم هيكل هاتف بالاسم (أضف صورة للحصول على نتيجة أفضل)", "warn")

        assets: list[dict] = []
        for cc in campaign.countries:
            lang = (campaign.languages or ["ar"])[0]
            copy = (campaign.copies.get(cc) or {}).get(lang) or {}
            if not copy:
                continue
            facts = facts_for(product, campaign, cc)
            arabic = lang == "ar"
            price = facts["price_ar"] if arabic else facts["price_en"]
            kwargs = dict(
                headline=(copy.get("headline") or "").replace(" | ", " ").strip() or (facts["product_name_ar"] if arabic else facts["product_name_en"]),
                price=price,
                cta=geo.country(cc)["cta"] if arabic else "Order now",
                specs=facts["short_specs"],
                product_img=img,
                label=facts["product_name_ar"] if arabic else facts["product_name_en"],
                font_override=self.settings.font_path,
                condition=facts["condition_ar"] if arabic else facts["condition_en"],
            )
            built = await asyncio.to_thread(self._build_country, out_dir, cc, kwargs, copy)
            for a in built:
                a["url"] = f"/media/{campaign.id}/{Path(a['path']).name}"
                a["country"] = cc
            assets += built
        self.log(db, campaign.id, f"تم إنتاج {len(assets)} ملف وسائط", "info",
                 images=sum(a["kind"] == "image" for a in assets), videos=sum(a["kind"] == "video" for a in assets))
        return assets

    # -- sync part (runs in a worker thread)
    def _build_country(self, out_dir: Path, cc: str, kw: dict, copy: dict) -> list[dict]:
        assets = []
        for ratio, (w, h) in {"1x1": (1080, 1080), "9x16": (1080, 1920)}.items():
            p = out_dir / f"{cc}_{ratio}.png"
            render_card(w, h, **kw).save(p, "PNG", optimize=True)
            assets.append({"kind": "image", "ratio": ratio.replace("x", ":"), "path": str(p)})
        try:
            video = self._build_video(out_dir, cc, kw)
            assets.append({"kind": "video", "ratio": "9:16", "path": str(video),
                           "duration": self.settings.video_seconds_per_scene * 3})
        except Exception as e:  # ffmpeg missing/failed: images alone are still a valid deliverable
            log.error("video build failed for %s: %s", cc, e)
            assets.append({"kind": "video_error", "ratio": "9:16", "path": "", "error": str(e)[:300]})
        return [a for a in assets if a["kind"] != "video_error"]

    def _build_video(self, out_dir: Path, cc: str, kw: dict) -> Path:
        w, h = 1080, 1920
        secs = max(2, self.settings.video_seconds_per_scene)
        fps = 25
        frames = secs * fps
        scenes = []
        for kind in ("hook", "specs", "offer"):
            p = out_dir / f"{cc}_scene_{kind}.png"
            render_scene(w, h, kind, **kw).save(p, "PNG")
            scenes.append(p)
        out = out_dir / f"{cc}_9x16.mp4"
        cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
        for p in scenes:
            cmd += ["-i", str(p)]
        music = self.settings.music_path
        has_music = bool(music) and Path(music).exists()
        if has_music:
            cmd += ["-i", music]
        parts = []
        for i in range(3):
            parts.append(
                f"[{i}:v]scale=1620:2880,zoompan=z='min(zoom+0.0007,1.07)':d={frames}"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},"
                f"fade=t=in:st=0:d=0.25,fade=t=out:st={secs - 0.25}:d=0.25,setsar=1[v{i}]")
        graph = ";".join(parts) + ";[v0][v1][v2]concat=n=3:v=1:a=0[v]"
        cmd += ["-filter_complex", graph, "-map", "[v]"]
        if has_music:
            cmd += ["-map", "3:a", "-c:a", "aac", "-b:a", "128k", "-shortest",
                    "-af", f"afade=t=out:st={secs * 3 - 1}:d=1"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
                "-movflags", "+faststart", str(out)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if res.returncode != 0:
            raise RuntimeError(res.stderr[-400:])
        for p in scenes:
            p.unlink(missing_ok=True)
        return out

    async def _load_product_image(self, product: Product) -> Image.Image | None:
        for ref in product.images or []:
            try:
                if ref.startswith("/uploads/"):
                    path = self.settings.data_dir / ref.lstrip("/")
                    if path.exists():
                        return Image.open(path).copy()
                elif ref.startswith(("http://", "https://")):
                    assert_public_url(ref)
                    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
                        r = await c.get(ref)
                        if r.status_code == 200 and len(r.content) < 15_000_000:
                            return Image.open(io.BytesIO(r.content)).copy()
            except (OSError, UnsafeURL, httpx.HTTPError) as e:
                log.warning("product image skipped (%s): %s", ref, e)
        return None
