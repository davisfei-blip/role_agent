import json
import re
from datetime import datetime
from urllib.parse import urlparse

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
}


def extract_first_url(candidate):
    if isinstance(candidate, str) and candidate.startswith("http"):
        return candidate
    if isinstance(candidate, list):
        for item in candidate:
            if isinstance(item, str) and item.startswith("http"):
                return item
    return None


def is_probably_video_url(url):
    if not url or not isinstance(url, str):
        return False
    lowered = url.lower()
    if lowered.endswith(".mp3"):
        return False
    if "ies-music" in lowered:
        return False
    if "music" in lowered and ".mp4" not in lowered:
        return False
    return True


def extract_image_urls(aweme_detail):
    image_groups = []
    for key in ("images", "image_infos", "photo_infos"):
        value = aweme_detail.get(key)
        if isinstance(value, list):
            image_groups.extend(value)

    image_post_info = aweme_detail.get("image_post_info", {})
    if isinstance(image_post_info, dict):
        for key in ("images", "image_infos"):
            value = image_post_info.get(key)
            if isinstance(value, list):
                image_groups.extend(value)

    urls = []
    for item in image_groups:
        candidates = [
            item.get("url"),
            item.get("url_list"),
            item.get("download_url_list"),
            item.get("origin_url_list"),
            (item.get("display_image") or {}).get("url_list"),
            (item.get("origin_image") or {}).get("url_list"),
            (item.get("download_image") or {}).get("url_list"),
            (item.get("thumbnail") or {}).get("url_list"),
        ]
        image_url = None
        for candidate in candidates:
            image_url = extract_first_url(candidate)
            if image_url:
                break
        if image_url and image_url not in urls:
            urls.append(image_url)

    return urls


def has_image_post_fields(aweme_detail):
    if isinstance(aweme_detail.get("images"), list) and aweme_detail.get("images"):
        return True
    if isinstance(aweme_detail.get("image_infos"), list) and aweme_detail.get("image_infos"):
        return True
    if isinstance(aweme_detail.get("photo_infos"), list) and aweme_detail.get("photo_infos"):
        return True
    image_post_info = aweme_detail.get("image_post_info")
    return isinstance(image_post_info, dict) and bool(image_post_info)


class DouyinResolver:
    def resolve_gid(self, gid):
        gid = self._normalize_gid(gid)
        aweme_detail = self._fetch_aweme_detail_by_id(gid)
        return self._build_resolved_content(gid, aweme_detail)

    def _normalize_gid(self, value):
        value = str(value or "").strip()
        if not value:
            raise ValueError("gid 为空")

        if value.isdigit():
            return value

        video_match = re.search(r"/video/(\d+)", value)
        note_match = re.search(r"/note/(\d+)", value)
        query_match = re.search(r"aweme_id=(\d+)", value)
        if video_match:
            return video_match.group(1)
        if note_match:
            return note_match.group(1)
        if query_match:
            return query_match.group(1)

        tail = urlparse(value).path.rstrip("/").split("/")[-1]
        if tail.isdigit():
            return tail

        raise ValueError(f"无法识别 gid: {value}")

    def _fetch_aweme_detail_by_id(self, gid):
        api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={gid}"
        response = requests.get(api_url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        data = response.json()
        aweme_detail = data.get("aweme_detail", data)
        if isinstance(aweme_detail, dict) and aweme_detail:
            return aweme_detail

        fallback_detail = self._fetch_aweme_detail_from_page(gid)
        if fallback_detail:
            return fallback_detail

        raise ValueError(
            f"详情接口未返回有效 aweme_detail: {gid}，且页面回退解析失败"
        )
        return aweme_detail

    def _fetch_aweme_detail_from_page(self, gid):
        for content_type in ("video", "note"):
            page_url = f"https://www.douyin.com/{content_type}/{gid}"
            response = requests.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            page_content = response.text

            data_match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", page_content)
            if not data_match:
                continue

            try:
                router_data = json.loads(data_match.group(1))
            except Exception:
                continue

            loader_data = router_data.get("loaderData", router_data)
            aweme_detail = (
                loader_data.get("video_(id)/page", {}).get("videoInfoRes", {}).get("item_list", [{}])[0]
                or loader_data.get("note_(id)/page", {}).get("videoInfoRes", {}).get("item_list", [{}])[0]
            )
            if isinstance(aweme_detail, dict) and aweme_detail:
                return aweme_detail

        return None

    def _build_resolved_content(self, gid, aweme_detail):
        video_info = aweme_detail.get("video") or {}
        play_addr = video_info.get("play_addr") or {}
        download_addr = video_info.get("download_addr") or {}

        video_url = extract_first_url(play_addr.get("url_list"))
        if video_url:
            video_url = video_url.replace("playwm", "play")
        if not video_url:
            video_url = extract_first_url(download_addr.get("url_list"))
        if not is_probably_video_url(video_url):
            video_url = None

        image_urls = extract_image_urls(aweme_detail)
        if has_image_post_fields(aweme_detail) and image_urls:
            content_type = "image_post"
        elif video_url:
            content_type = "video"
        elif image_urls:
            content_type = "image_post"
        else:
            content_type = "unknown"

        desc = (aweme_detail.get("desc") or "").strip()
        author = (aweme_detail.get("author") or {}).get("nickname", "")
        aweme_id = str(aweme_detail.get("aweme_id") or gid)
        web_url = f"https://www.douyin.com/video/{aweme_id}" if content_type == "video" else f"https://www.douyin.com/note/{aweme_id}"
        cover_url = extract_first_url(((video_info.get("cover") or {}).get("url_list")))
        if not cover_url and image_urls:
            cover_url = image_urls[0]
        music_info = aweme_detail.get("music") or {}
        music_url = (
            extract_first_url(((music_info.get("play_url") or {}).get("url_list")))
            or extract_first_url(((music_info.get("play_url_hd") or {}).get("url_list")))
        )

        statistics = aweme_detail.get("statistics") or {}
        stat_parts = []
        for label, key in [("点赞", "digg_count"), ("评论", "comment_count"), ("收藏", "collect_count"), ("分享", "share_count")]:
            value = statistics.get(key)
            if value is not None:
                stat_parts.append(f"{label}: {value}")

        text_extra = aweme_detail.get("text_extra") or []
        tags = []
        for item in text_extra:
            hashtag = (item or {}).get("hashtag_name")
            if hashtag:
                tags.append(hashtag)

        created_at = aweme_detail.get("create_time")
        created_text = ""
        if created_at:
            try:
                created_text = datetime.fromtimestamp(int(created_at)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                created_text = str(created_at)

        title = desc or f"douyin_{aweme_id}"
        content_lines = [
            f"内容类型: {content_type}",
            f"作者: {author or '未知'}",
            f"作品ID: {aweme_id}",
            f"发布时间: {created_text or '未知'}",
            f"文案: {desc or '无'}",
        ]
        if tags:
            content_lines.append(f"话题: {', '.join(tags[:10])}")
        if stat_parts:
            content_lines.append(f"互动数据: {'; '.join(stat_parts)}")
        if content_type == "image_post":
            content_lines.append(f"图片数: {len(image_urls)}")
        if content_type == "video" and video_url:
            content_lines.append("存在视频内容，可进一步结合视频工具做画面理解。")
        if web_url:
            content_lines.append(f"页面链接: {web_url}")

        return {
            "gid": gid,
            "aweme_id": aweme_id,
            "title": title,
            "content": "\n".join(content_lines),
            "content_type": content_type,
            "desc": desc,
            "author": author,
            "video_url": video_url,
            "image_urls": image_urls,
            "cover_url": cover_url,
            "music_url": music_url,
            "web_url": web_url,
            "raw": aweme_detail,
        }
