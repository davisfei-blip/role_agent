import subprocess

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
}


class DouyinMediaStore:
    def __init__(self, dataset_store):
        self.dataset_store = dataset_store

    def ensure_local_assets(self, config_key, gid, extracted):
        content_type = extracted.get("content_type")
        result = {
            "content_type": content_type,
            "video_path": None,
            "audio_path": None,
            "cover_path": None,
            "image_paths": [],
        }

        if content_type == "video":
            video_path = self.dataset_store.source_video_path(config_key, gid)
            audio_path = self.dataset_store.audio_path(config_key, gid)
            cover_path = self.dataset_store.cover_path(config_key, gid)

            if not video_path.exists():
                self._download_file(extracted.get("video_url"), video_path)
            if video_path.exists() and video_path.stat().st_size == 0:
                self._cleanup_file(video_path)
                raise ValueError("下载到的原始视频为空文件，可能不是有效视频地址")

            if video_path.exists() and not audio_path.exists():
                self._extract_audio(video_path, audio_path)
            if not cover_path.exists() and extracted.get("cover_url"):
                self._download_file(extracted.get("cover_url"), cover_path)

            result.update({
                "video_path": str(video_path) if video_path.exists() else None,
                "audio_path": str(audio_path) if audio_path.exists() else None,
                "cover_path": str(cover_path) if cover_path.exists() else None,
            })
            return result

        if content_type == "image_post":
            image_paths = []
            images_dir = self.dataset_store.images_dir(config_key, gid)
            for index, image_url in enumerate(extracted.get("image_urls") or [], start=1):
                suffix = self._guess_extension(image_url)
                image_path = images_dir / f"image_{index:02d}{suffix}"
                if not image_path.exists():
                    self._download_file(image_url, image_path)
                image_paths.append(str(image_path))

            audio_path = self.dataset_store.audio_path(config_key, gid)
            if extracted.get("music_url") and not audio_path.exists():
                self._download_file(extracted.get("music_url"), audio_path)

            cover_path = self.dataset_store.cover_path(config_key, gid)
            if not cover_path.exists():
                cover_source = extracted.get("cover_url") or (extracted.get("image_urls") or [None])[0]
                if cover_source:
                    self._download_file(cover_source, cover_path)

            result.update({
                "audio_path": str(audio_path) if audio_path.exists() else None,
                "cover_path": str(cover_path) if cover_path.exists() else None,
                "image_paths": image_paths,
            })
            return result

        return result

    def _download_file(self, url, filepath):
        if not url:
            raise ValueError("缺少可下载的素材地址")

        response = requests.get(url, headers=HEADERS, stream=True, timeout=120)
        if 300 <= response.status_code < 400 and response.headers.get("location"):
            return self._download_file(response.headers["location"], filepath)
        response.raise_for_status()

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)

    def _extract_audio(self, video_path, audio_path):
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-acodec",
                    "libmp3lame",
                    "-q:a",
                    "0",
                    "-y",
                    str(audio_path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"提取音频失败: {exc.stderr}") from exc

    def _guess_extension(self, url):
        lowered = (url or "").lower()
        for suffix in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"]:
            if suffix in lowered:
                return suffix
        if ".mp3" in lowered:
            return ".mp3"
        return ".jpg"

    def _cleanup_file(self, path):
        if path.exists():
            path.unlink()
