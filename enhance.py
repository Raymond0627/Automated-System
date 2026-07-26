import cv2
import numpy as np
import tempfile
import os
import fitz


def enhance_page(page, dpi=150, config=None):
    if config is None:
        config = {}
    if not config.get("enhance_enabled", True):
        return False

    dpi = config.get("enhance_dpi", 0) or dpi
    clahe_clip = config.get("enhance_bw_clahe_clip", 2.0)
    clahe_tile = config.get("enhance_bw_clahe_tile", 8)
    denoise_strength = config.get("enhance_denoise_strength", 5)
    denoise_template = config.get("enhance_denoise_template", 7)
    denoise_search = config.get("enhance_denoise_search", 21)
    sharpen_amount = config.get("enhance_sharpen_amount", 0.5)
    sharpen_blur = config.get("enhance_sharpen_blur", 1.0)
    white_point = config.get("enhance_white_point", 98.0)
    color_sat_threshold = config.get("enhance_sat_threshold", 15)
    color_pct = config.get("enhance_color_pct", 2.0)
    color_clahe_clip = config.get("enhance_color_clahe_clip", 2.0)
    color_clahe_tile = config.get("enhance_color_clahe_tile", 8)

    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    colored_pixels = np.sum(saturation > color_sat_threshold)
    total_pixels = saturation.shape[0] * saturation.shape[1]
    is_color = (colored_pixels / max(total_pixels, 1)) > (color_pct / 100.0)

    if is_color:
        lab = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=color_clahe_clip, tileGridSize=(color_clahe_tile, color_clahe_tile))
        l_enhanced = clahe.apply(l)
        enhanced = cv2.merge([l_enhanced, a, b])
        enhanced_rgb = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    else:
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2GRAY)
        if denoise_strength > 0:
            gray = cv2.fastNlMeansDenoising(gray, None, denoise_strength, denoise_template, denoise_search)
        clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_tile, clahe_tile))
        enhanced_gray = clahe.apply(gray)
        if white_point < 100.0:
            threshold_val = np.percentile(enhanced_gray, white_point)
            if threshold_val > 200:
                enhanced_gray = np.clip(enhanced_gray.astype(float) * (255.0 / threshold_val), 0, 255).astype(np.uint8)
        if sharpen_amount > 0:
            blurred = cv2.GaussianBlur(enhanced_gray, (0, 0), sharpen_blur)
            enhanced_gray = cv2.addWeighted(enhanced_gray, 1.0 + sharpen_amount, blurred, -sharpen_amount, 0)
        enhanced_rgb = cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp.name, enhanced_rgb)
    rect = page.rect
    img_pix = fitz.Pixmap(tmp.name)
    scale = min(rect.width / max(img_pix.width, 1), rect.height / max(img_pix.height, 1))
    w, h = int(img_pix.width * scale), int(img_pix.height * scale)
    x = (rect.width - w) / 2
    y = (rect.height - h) / 2
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.insert_image(fitz.Rect(x, y, x + w, y + h), pixmap=img_pix)
    img_pix = None
    tmp.close()
    os.unlink(tmp.name)
    return True
