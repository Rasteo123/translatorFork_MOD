# perplexity_uploader.py
"""
PERPLEXITY UPLOADER - v3.2 (COMPATIBILITY)
Sentinel Edition: List-to-Dict Multipart Fix
"""

import os
import uuid
import json
import logging
import tempfile
import mimetypes
from typing import Dict, Any

from curl_cffi import requests as cffi_requests

logger = logging.getLogger("PerplexityUploader")


class PerplexityUploader:
    def __init__(self, session: cffi_requests.Session, token: str):
        if not token:
            raise ValueError(
                "Security Violation: valid session token required.")

        self.session = session
        self.token = token

        self.base_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://www.perplexity.ai",
            "Referer": "https://www.perplexity.ai/",
        }

    # === PUBLIC API ===

    def upload_text_as_file(self, text: str, filename: str = "large_context.txt") -> Dict[str, Any]:
        """
        Ставит текст во временный файл и загружает его в Perplexity
        через общий upload_file-пайплайн.
        """
        tf = tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8", delete=False, suffix=".txt"
        )
        try:
            tf.write(text)
            tf.close()
            logger.info(
                f"📝 Staged text payload ({len(text)} chars) to {tf.name}")
            return self.upload_file(tf.name, filename)
        except Exception as e:
            logger.error(f"Text staging failed: {e}")
            raise
        finally:
            self._secure_delete(tf.name)

    def upload_file(self, file_path: str, filename: str) -> Dict[str, Any]:
        """
        Полный пайплайн:
        1) Handshake с Perplexity (batch_create_upload_urls)
        2) Загрузка файла в S3
        3) attachment_processing_subscribe
        4) Построение final_url (именно его ожидает API в attachments)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Target file not found: {file_path}")

        file_size = os.path.getsize(file_path)
        file_uuid = str(uuid.uuid4())
        real_mime = mimetypes.guess_type(file_path)[0] or "text/plain"

        logger.info(
            f"🚀 Starting upload: {filename} ({file_size} bytes) | UUID: {file_uuid}")

        try:
            # STEP 1: Handshake
            s3_data = self._initiate_upload(filename, file_uuid, file_size)

            # STEP 2: S3 Upload
            self._perform_s3_upload(file_path, s3_data, real_mime)

            # STEP 3: Subscribe
            self._subscribe_processing(file_uuid)

            # STEP 4: URL Construction
            fields = s3_data.get("fields", {})
            key = fields.get("key")

            base_url = (
                s3_data.get("url")
                or s3_data.get("s3_bucket_url")
                or "https://ppl-ai-file-upload.s3.amazonaws.com"
            )

            if base_url.endswith("/"):
                final_url = f"{base_url}{key}"
            else:
                final_url = f"{base_url}/{key}"

            final_url = final_url.replace("com//", "com/")

            logger.info(f"✅ Upload success. URL: {final_url}")

            return {
                "url": final_url,
                "file_uuid": file_uuid,
                "filename": filename,
                "size": file_size,
                "mime_type": real_mime,
            }

        except Exception as e:
            logger.critical(f"Upload Pipeline Failed: {e}")
            raise

    # === INTERNAL STEPS ===

    def _initiate_upload(self, filename: str, file_uuid: str, file_size: int) -> Dict[str, Any]:
        """
        Вызывает /rest/uploads/batch_create_upload_urls и вытаскивает
        конфиг для прямой загрузки в S3.
        """
        url = "https://www.perplexity.ai/rest/uploads/batch_create_upload_urls"
        params = {"version": "2.18", "source": "default"}

        file_meta = {
            "file_uuid": file_uuid,
            "filename": filename,
            "content_type": "",
            "source": "default",
            "file_size": file_size,
            "force_image": False,
        }

        payload = {
            "files": {
                file_uuid: file_meta
            }
        }

        resp = self.session.post(
            url,
            params=params,
            data=json.dumps(payload, separators=(",", ":")),
            headers=self.base_headers,
            cookies={"__Secure-next-auth.session-token": self.token},
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error(f"Handshake failed: {resp.text}")
            raise RuntimeError(f"Handshake failed: {resp.text}")

        data = resp.json()

        if "results" in data and file_uuid in data["results"]:
            cfg = data["results"][file_uuid]
            if "s3_bucket_url" in cfg:
                cfg["url"] = cfg["s3_bucket_url"]
            return cfg

        if file_uuid in data:
            return data[file_uuid]

        if "uploads" in data and len(data["uploads"]) > 0:
            return data["uploads"][0]

        found = self._find_config_recursive(data)
        if found:
            return found

        raise ValueError(
            f"Could not parse S3 config from: {json.dumps(data)[:200]}")

    def _find_config_recursive(self, obj):
        """
        Рекурсивный поиск структуры вида {fields: {...}, url/s3_bucket_url: "..."}.
        """
        if isinstance(obj, dict):
            if "fields" in obj and ("url" in obj or "s3_bucket_url" in obj):
                if "s3_bucket_url" in obj:
                    obj["url"] = obj["s3_bucket_url"]
                return obj
            for v in obj.values():
                res = self._find_config_recursive(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for v in obj:
                res = self._find_config_recursive(v)
                if res:
                    return res
        return None

    def _perform_s3_upload(self, file_path: str, s3_data: Dict[str, Any], mime_type: str):
        """
        Реальная отправка файла на S3. Для curl_cffi используем multipart=...
        и явно создаём CurlMime совместимый payload через list of dicts.
        """
        upload_url = s3_data.get("url")
        fields = s3_data.get("fields")

        if not upload_url or not fields:
            raise ValueError("S3 data missing 'url' or 'fields'")

        # curl_cffi ожидает multipart= CurlMime или list[dict]; используем from_list.[web:1]
        from curl_cffi import CurlMime  # type: ignore

        parts = []

        # Сначала все текстовые поля
        for name, value in fields.items():
            parts.append(
                {
                    "name": name,
                    "data": str(value).encode("utf-8"),
                }
            )

        # Файл — отдельной частью, последней
        with open(file_path, "rb") as f:
            parts.append(
                {
                    "name": "file",
                    "filename": os.path.basename(file_path),
                    "content_type": mime_type,
                    "data": f.read(),
                }
            )

        mp = CurlMime.from_list(parts)

        try:
            resp = self.session.post(
                upload_url,
                multipart=mp,
                headers={"User-Agent": self.base_headers["User-Agent"]},
                timeout=60,
            )
        finally:
            mp.close()

        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f"S3 Upload failed (HTTP {resp.status_code}): {resp.text}"
            )

    def _subscribe_processing(self, file_uuid: str):
        """
        Подписка на обработку аттача. Ошибки игнорируются (как в браузере).
        """
        try:
            self.session.post(
                "https://www.perplexity.ai/rest/sse/attachment_processing_subscribe",
                json={"file_uuids": [file_uuid]},
                headers=self.base_headers,
                cookies={"__Secure-next-auth.session-token": self.token},
                timeout=10,
            )
        except Exception:
            # SSE subscribe не критичен для стабильности пайплайна
            pass

    def _secure_delete(self, path: str):
        """
        Безопасное удаление временного файла.
        """
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
