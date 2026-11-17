"""Format selection helpers."""


def filter_video_formats(formats: list[dict]) -> list[dict]:
    """
    One format per resolution, best first.

    Within a resolution, prefer direct HTTP/DASH over HLS (m3u8 is much slower
    to download); between two of the same kind, keep the higher bitrate.
    """
    seen: dict[int, dict] = {}
    for fmt in formats:
        if fmt.get("vcodec", "none") == "none" or not fmt.get("height"):
            continue
        h = fmt["height"]
        is_m3u8 = "m3u8" in (fmt.get("protocol") or "")

        prev = seen.get(h)
        if prev is None:
            seen[h] = fmt
            continue
        prev_is_m3u8 = "m3u8" in (prev.get("protocol") or "")
        if prev_is_m3u8 and not is_m3u8:
            seen[h] = fmt
        elif prev_is_m3u8 == is_m3u8 and (fmt.get("tbr") or 0) > (prev.get("tbr") or 0):
            seen[h] = fmt
    return sorted(seen.values(), key=lambda f: f.get("height", 0), reverse=True)
