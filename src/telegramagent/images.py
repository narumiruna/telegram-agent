from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ImageAttachment:
    data: bytes
    media_type: str
    filename: str = "image"


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    media_type: str = "image/png"
    filename: str = "generated-image.png"


class ImageGenerationError(RuntimeError):
    """Raised when an image generation provider returns no usable image."""


class OpenAIImageGenerator:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        size: str = "1024x1024",
        timeout_seconds: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.size = size
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str) -> GeneratedImage:
        if not self.api_key:
            raise ImageGenerationError("OPENAI_API_KEY is not configured")
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "size": self.size,
            "n": 1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                return await self._post_generation(client, payload, headers)
        return await self._post_generation(self.http_client, payload, headers)

    async def _post_generation(
        self,
        client: httpx.AsyncClient,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> GeneratedImage:
        response = await client.post(f"{self.base_url}/images/generations", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise ImageGenerationError("image generation response was not an object")
        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise ImageGenerationError("image generation response did not include image data")
        first = items[0]
        if not isinstance(first, Mapping):
            raise ImageGenerationError("image generation item was not an object")

        b64_json = first.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            try:
                image_data = base64.b64decode(b64_json, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImageGenerationError("image generation response contained invalid base64 data") from exc
            return GeneratedImage(data=image_data, media_type="image/png")

        url = first.get("url")
        if isinstance(url, str) and url.strip():
            return await self._download_generated_image(client, url)

        raise ImageGenerationError("image generation response did not include b64_json or url")

    @staticmethod
    async def _download_generated_image(client: httpx.AsyncClient, url: str) -> GeneratedImage:
        response = await client.get(url)
        response.raise_for_status()
        media_type = response.headers.get("content-type", "image/png").split(";", maxsplit=1)[0].strip()
        if not media_type.startswith("image/"):
            media_type = "image/png"
        filename = _filename_for_media_type(media_type)
        return GeneratedImage(data=response.content, media_type=media_type, filename=filename)


def _filename_for_media_type(media_type: str) -> str:
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(media_type, "png")
    return f"generated-image.{extension}"
