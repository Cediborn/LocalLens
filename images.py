"""
LocalLens - Real per-place images.

Only images that are *actually associated* with the specific place are used:
  1. explicit OSM image / wikimedia_commons / photo tags
  2. Wikidata P18 (image) resolved from the place's wikidata tag

Commons name-search is deliberately NOT used - matching a generic photo by
name can attach an unrelated image to a business, which destroys trust.

If no trustworthy image is available, the frontend shows a clean monogram
placeholder (clearly not a photo of the place) instead of a generic stock photo.
"""

from __future__ import annotations

import re
import time
import hashlib
import json
from typing import Optional
from urllib.parse import quote

import httpx

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath"

_media_cache: dict = {}
_MEDIA_CACHE_MAX_AGE = 30 * 86400  # 30 days - images rarely change
_MEDIA_CACHE_MAX_SIZE = 600


def _cache_key(prefix: str, args: dict) -> str:
    raw = json.dumps(args, sort_keys=True, default=str)
    return f"{prefix}:{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def _cache_get(key: str):
    entry = _media_cache.get(key)
    if entry is None or time.time() - entry["timestamp"] > _MEDIA_CACHE_MAX_AGE:
        return None
    return entry["data"]


def _cache_set(key: str, data):
    if len(_media_cache) >= _MEDIA_CACHE_MAX_SIZE:
        oldest = min(_media_cache.items(), key=lambda x: x[1]["timestamp"])
        del _media_cache[oldest[0]]
    _media_cache[key] = {"data": data, "timestamp": time.time()}


def _commons_thumb(file_title: str, width: int = 640) -> str:
    """Build a Special:FilePath thumbnail URL for a Commons file name."""
    name = (file_title or "").strip()
    if not name:
        return ""
    # Accept both 'File:X.jpg' and bare 'X.jpg'
    fn = re.sub(r'^file\s*:', '', name, flags=re.IGNORECASE)
    return f"{COMMONS_FILEPATH}/{quote(fn, safe='() _,.-')}?width={width}"


def _image_from_osm_tags(tags: dict, width: int = 640) -> Optional[dict]:
    """Extract an image URL from direct OSM image tags, when present."""
    commons = tags.get("wikimedia_commons") or tags.get("wikidata_commons")
    if commons:
        url = _commons_thumb(commons, width)
        if url:
            return {
                "thumb_url": url, "width": width,
                "source": "Wikimedia Commons (OSM tag)",
                "attribution": "Photo from Wikimedia Commons (CC) - via OpenStreetMap",
            }

    image_tag = tags.get("image") or tags.get("photo") or tags.get("source") or ""
    image_tag = image_tag.strip()
    if image_tag:
        low = image_tag.lower()
        # Direct https image URL
        if low.startswith("https://") or low.startswith("http://"):
            if re.search(r'\.(png|jpe?g|gif|webp)([\?&#]|$)', low):
                return {
                    "thumb_url": image_tag, "width": None,
                    "source": "OSM image tag",
                    "attribution": "Image from OpenStreetMap contributor",
                }
        # Wikimedia Commons file page URL e.g. .../wiki/File:Accra.jpg
        m = re.search(r'/wiki/(File:[^\s?&]+)', image_tag, re.IGNORECASE)
        if m:
            url = _commons_thumb(m.group(1), width)
            if url:
                return {
                    "thumb_url": url, "width": width,
                    "source": "Wikimedia Commons (OSM tag)",
                    "attribution": "Photo from Wikimedia Commons (CC) - via OpenStreetMap",
                }
    return None


async def _wikidata_image(qid: str, client: httpx.AsyncClient,
                          width: int = 640) -> Optional[dict]:
    """Resolve the Wikidata P18 (image) claim for an entity."""
    key = _cache_key("wimg", {"qid": qid, "w": width})
    cached = _cache_get(key)
    if cached is not None:
        return cached if cached else None

    try:
        params = {
            "action": "wbgetclaims", "entity": qid,
            "property": "P18", "format": "json", "origin": "*",
        }
        resp = await client.get(WIKIDATA_API, params=params, timeout=6.0)
        if resp.status_code == 200:
            data = resp.json()
            claims = (data.get("claims") or {}).get("P18") or []
            if claims:
                main = claims[0].get("mainsnak", {})
                value = (main.get("datavalue") or {}).get("value")
                if value:
                    url = _commons_thumb(f"File:{value}", width)
                    if url:
                        result = {
                            "thumb_url": url, "width": width,
                            "source": "Wikimedia Commons via Wikidata",
                            "attribution": "Photo from Wikimedia Commons (CC) - via Wikidata",
                        }
                        _cache_set(key, result)
                        return result
        _cache_set(key, None)
    except Exception:
        _cache_set(key, None)
    return None


async def get_place_image(tags: dict, client: httpx.AsyncClient,
                          width: int = 640) -> Optional[dict]:
    """
    Find a real image for a place from its OSM tags and Wikidata, in order of
    trustworthiness. Returns None when nothing trustworthy is available.
    """
    # Strongest: direct OSM tags. Checked first, no network needed.
    from_tags = _image_from_osm_tags(tags, width)
    if from_tags:
        return from_tags

    # Medium: wikidata entity → its image claim.
    qid = (tags.get("wikidata") or tags.get("brand:wikidata") or "").strip()
    if qid and re.match(r'^Q\d+$', qid):
        return await _wikidata_image(qid, client, width)

    return None


async def enrich_images(places: list, max_places: int = 20,
                        concurrency: int = 6) -> dict:
    """
    Enrich the top places with real images. Returns {osm_index: image_info}.

    Errors are swallowed - an image is a nice-to-have, never a blocker.
    """
    import asyncio

    targets = [(i, p) for i, p in enumerate(places)
               if (p.get("tags", {}).get("image") or
                   p.get("tags", {}).get("wikimedia_commons") or
                   p.get("tags", {}).get("photo") or
                   p.get("tags", {}).get("wikidata") or
                   p.get("tags", {}).get("brand:wikidata"))]
    targets = targets[:max_places]

    if not targets:
        return {}

    sem = asyncio.Semaphore(concurrency)
    results: dict = {}

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency),
                                 timeout=6.0) as client:
        async def one(item):
            i, place = item
            try:
                async with sem:
                    img = await get_place_image(place.get("tags", {}), client)
                    if img:
                        results[i] = img
            except Exception:
                pass

        await asyncio.gather(*(one(item) for item in targets))

    return results