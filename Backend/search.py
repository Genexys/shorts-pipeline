import os

import requests

from itertools import zip_longest
from typing import Dict, List

from logstream import log

PEXELS_TIMEOUT = 60
PIXABAY_TIMEOUT = 60
# Used for fetching the clips themselves, whichever library they came from.
DOWNLOAD_TIMEOUT = 60


def _best_pexels_url(video_files: List[Dict]) -> str:
    """The highest-resolution downloadable file in a Pexels result."""
    best_url, best_pixels = "", 0
    for candidate in video_files:
        link = candidate.get("link", "")
        if ".com/video-files" not in link:
            continue
        pixels = (candidate.get("width") or 0) * (candidate.get("height") or 0)
        if pixels > best_pixels:
            best_url, best_pixels = link, pixels
    return best_url


def search_pexels(query: str, api_key: str, it: int, min_dur: int) -> List[str]:
    """
    Searches Pexels for stock videos.

    Args:
        query (str): The query to search for.
        api_key (str): The API key to use.
        it (int): How many results to ask for.
        min_dur (int): Shortest acceptable clip, in seconds.

    Returns:
        List[str]: Download URLs, best resolution first within each result.
    """
    if not api_key:
        return []
    try:
        response = requests.get(
            f"https://api.pexels.com/videos/search?query={query}&per_page={it}",
            headers={"Authorization": api_key},
            timeout=PEXELS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:
        log(f"[-] Pexels search failed for '{query}': {err}", "warning")
        return []

    urls = []
    for item in payload.get("videos") or []:
        if (item.get("duration") or 0) < min_dur:
            continue
        url = _best_pexels_url(item.get("video_files") or [])
        if url:
            urls.append(url)
    return urls


def search_pixabay(query: str, api_key: str, it: int, min_dur: int) -> List[str]:
    """
    Searches Pixabay for stock videos.

    A second library rather than more of the same one: on a narrow subject
    Pexels runs out of distinct results long before a long video has enough
    footage, and the two catalogues barely overlap.

    Args:
        query (str): The query to search for.
        api_key (str): The API key to use. Empty disables this source.
        it (int): How many results to ask for.
        min_dur (int): Shortest acceptable clip, in seconds.

    Returns:
        List[str]: Download URLs. Empty when unconfigured or on any failure.
    """
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": api_key, "q": query, "per_page": max(it, 3)},
            timeout=PIXABAY_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:
        log(f"[-] Pixabay search failed for '{query}': {err}", "warning")
        return []

    urls = []
    for item in payload.get("hits") or []:
        if (item.get("duration") or 0) < min_dur:
            continue
        renditions = item.get("videos") or {}
        best_url, best_pixels = "", 0
        for rendition in renditions.values():
            pixels = (rendition.get("width") or 0) * (rendition.get("height") or 0)
            if rendition.get("url") and pixels > best_pixels:
                best_url, best_pixels = rendition["url"], pixels
        if best_url:
            urls.append(best_url)
    return urls


def interleave(*sources: List[str]) -> List[str]:
    """Alternates between result lists, dropping duplicates.

    Concatenating instead would let the first library fill the video on its
    own whenever it returns enough, which defeats having a second one.
    """
    merged, seen = [], set()
    for group in zip_longest(*sources):
        for url in group:
            if url and url not in seen:
                merged.append(url)
                seen.add(url)
    return merged


def search_for_stock_videos(query: str, api_key: str, it: int, min_dur: int) -> List[str]:
    """
    Searches every configured stock library and interleaves the results.

    Args:
        query (str): The query to search for.
        api_key (str): Pexels API key.
        it (int): How many results to ask each library for.
        min_dur (int): Shortest acceptable clip, in seconds.

    Returns:
        List[str]: Download URLs from all libraries, interleaved.
    """
    pexels = search_pexels(query, api_key, it, min_dur)
    pixabay = search_pixabay(query, os.getenv("PIXABAY_API_KEY", "").strip(), it, min_dur)
    urls = interleave(pexels, pixabay)
    log(
        f'\t=> "{query}" found {len(urls)} videos '
        f"({len(pexels)} pexels, {len(pixabay)} pixabay)",
        "info",
    )
    return urls
