from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

import telegramagent.images as images_module
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageGenerationError
from telegramagent.images import OpenAIImageGenerator
from telegramagent.images import as_telegram_photo


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


def test_as_telegram_photo_keeps_supported_image_within_dimension_limit_unchanged() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 4), "white").save(buffer, format="PNG")
    image = GeneratedImage(data=buffer.getvalue(), media_type="image/png", filename="chart.png")

    assert as_telegram_photo(image) is image


def test_as_telegram_photo_resizes_image_that_exceeds_dimension_limit(monkeypatch) -> None:
    monkeypatch.setattr(images_module, "_TELEGRAM_PHOTO_MAX_DIMENSION_SUM", 10)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 4), "white").save(buffer, format="PNG")
    image = GeneratedImage(data=buffer.getvalue(), media_type="image/png", filename="chart.png")

    resized = as_telegram_photo(image)

    with Image.open(io.BytesIO(resized.data)) as opened_image:
        assert opened_image.size == (6, 3)
    assert resized.media_type == "image/png"
    assert resized.filename == "chart.png"


@pytest.mark.asyncio
async def test_openai_image_generator_rejects_missing_image_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        generator = OpenAIImageGenerator(api_key="key", model="image-model", http_client=client)
        with pytest.raises(ImageGenerationError):
            await generator.generate("一隻貓")
