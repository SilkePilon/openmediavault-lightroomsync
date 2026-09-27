"""Optional local post-processing of the downloaded JPEG renditions."""

import io


def process(data: bytes, max_dimension: int, quality: int, keep_metadata: bool) -> bytes:
    """Downscale to `max_dimension` on the long edge and/or strip metadata.

    The rendition bytes are returned untouched when nothing needs to change,
    so the default settings never re-encode (and never lose quality).
    """
    if not max_dimension and keep_metadata:
        return data
    # Imported lazily: Pillow is only needed when post-processing is enabled.
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        too_big = max_dimension and max(img.size) > max_dimension
        if not too_big and keep_metadata:
            return data
        exif = img.info.get("exif") if keep_metadata else None
        icc = img.info.get("icc_profile")
        out_img = img
        if too_big:
            if not keep_metadata:
                # Without the EXIF orientation tag the pixels must be
                # rotated, otherwise the photo would display sideways.
                out_img = ImageOps.exif_transpose(img)
            out_img = out_img.copy()
            out_img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
        elif not keep_metadata:
            out_img = ImageOps.exif_transpose(img)
        if out_img.mode not in ("RGB", "L"):
            out_img = out_img.convert("RGB")
        buf = io.BytesIO()
        kwargs = {"quality": quality, "optimize": True}
        if exif:
            kwargs["exif"] = exif
        if icc:
            # Keep the colour profile even when stripping metadata; dropping
            # it would shift colours for wide-gamut renditions.
            kwargs["icc_profile"] = icc
        out_img.save(buf, "JPEG", **kwargs)
        return buf.getvalue()
