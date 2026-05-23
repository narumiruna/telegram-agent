from __future__ import annotations

import base64

import httpx
import pytest

from telegramagent.images import ImageGenerationError
from telegramagent.images import OpenAIImageGenerator


@pytest.mark.asyncio
async def test_openai_image_generator_decodes_b64_json() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), request.read().decode()))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"png").decode()}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        generator = OpenAIImageGenerator(
            api_key="key",
            model="image-model",
            base_url="https://example.test/v1",
            size="512x512",
            http_client=client,
        )
        image = await generator.generate("一隻貓")

    assert image.data == b"png"
    assert image.media_type == "image/png"
    assert requests[0][0] == "https://example.test/v1/images/generations"
    assert '"model":"image-model"' in requests[0][1]
    assert '"size":"512x512"' in requests[0][1]


@pytest.mark.asyncio
async def test_openai_image_generator_downloads_url_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.test/image.webp"}]})
        return httpx.Response(200, headers={"content-type": "image/webp"}, content=b"webp")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        generator = OpenAIImageGenerator(
            api_key="key",
            model="image-model",
            base_url="https://example.test/v1",
            http_client=client,
        )
        image = await generator.generate("一隻貓")

    assert image.data == b"webp"
    assert image.media_type == "image/webp"
    assert image.filename == "generated-image.webp"


@pytest.mark.asyncio
async def test_openai_image_generator_rejects_missing_image_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        generator = OpenAIImageGenerator(api_key="key", model="image-model", http_client=client)
        with pytest.raises(ImageGenerationError):
            await generator.generate("一隻貓")
