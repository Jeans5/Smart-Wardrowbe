"""Automatic product photos (BG_REMOVAL_PROVIDER=gemini).

Covers the Gemini provider's request, response parsing, retries and fallbacks against a
mock transport; ImageService reading the untouched original; the image job's automatic
mode; queueing after tagging and after a replaced photo; and the features endpoint.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.item import ClothingItem, ItemStatus, ProcessingKind
from app.models.user import User
from app.services import background_removal as bgr
from app.services.ai_service import ClothingTags
from app.services.background_removal import (
    GeminiProductPhotoProvider,
    ProductPhotoError,
    build_prompt,
    extract_image,
)
from app.services.image_service import ImageService
from app.workers import background_removal as bg_worker
from app.workers import tagging
from app.workers.queues import IMAGE_QUEUE

GEMINI = Settings(bg_removal_provider="gemini", ai_api_key="test-key")
RED = (220, 20, 20)
GREEN = (10, 200, 30)


def _png_b64(color=RED, size=(64, 64)) -> str:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _jpeg_bytes(size=(400, 600), color=GREEN) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _noise_jpeg(seed: int) -> bytes:
    # Distinct content, so the upload's duplicate check (perceptual hash) keeps both.
    import random

    rng = random.Random(seed)
    image = Image.new("RGB", (64, 64))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(64 * 64)])
    buf = BytesIO()
    image.resize((400, 400)).save(buf, format="JPEG")
    return buf.getvalue()


def _image_response(color=RED, extra_parts=None) -> dict:
    parts = list(extra_parts or [])
    parts.append({"inlineData": {"mimeType": "image/png", "data": _png_b64(color)}})
    return {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}


class _Recorder:
    """A MockTransport handler that replays queued responses and keeps every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        status, body = response
        return httpx.Response(status, json=body)

    def body(self, index=-1) -> dict:
        return json.loads(self.requests[index].content)


def _provider(recorder, **kwargs) -> GeminiProductPhotoProvider:
    defaults = {"api_key": "secret", "model": "gemini-3.1-flash-image-preview"}
    defaults.update(kwargs)
    return GeminiProductPhotoProvider(transport=httpx.MockTransport(recorder), **defaults)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bgr.time, "sleep", lambda _s: None)


class TestPrompt:
    def test_white_background_is_named_explicitly(self):
        prompt = build_prompt((255, 255, 255))
        assert "pure white (#FFFFFF)" in prompt
        assert "ironed" in prompt
        assert "Do not add, remove, redesign or recolour anything" in prompt

    def test_other_colour_and_hint(self):
        prompt = build_prompt((0, 0, 0), hint="- Item: blazer\n- Material: wool")
        assert "#000000" in prompt
        assert prompt.endswith("the photo wins):\n- Item: blazer\n- Material: wool")

    def test_fabric_gets_its_own_instructions(self):
        prompt = build_prompt((255, 255, 255))
        assert "The fabric matters most." in prompt
        assert "weave or knit" in prompt
        assert "does not remove the fabric's own texture" in prompt

    def test_close_up_is_explained_only_when_sent(self):
        assert "second image is a close-up" in build_prompt((255, 255, 255), close_up=True)
        assert "second image" not in build_prompt((255, 255, 255))


class TestExtractImage:
    def test_takes_the_last_non_thought_image(self):
        payload = _image_response(
            RED,
            extra_parts=[
                {"text": "thinking"},
                {"thought": True, "inlineData": {"mimeType": "image/png", "data": _png_b64(GREEN)}},
            ],
        )
        payload["candidates"][0]["content"]["parts"].append(
            {"thought": True, "inlineData": {"mimeType": "image/png", "data": _png_b64(GREEN)}}
        )
        image = extract_image(payload)
        assert image.mode == "RGBA"
        assert image.getpixel((5, 5))[:3] == RED

    def test_blocked_prompt_is_not_retryable(self):
        with pytest.raises(ProductPhotoError) as exc:
            extract_image({"promptFeedback": {"blockReason": "SAFETY"}})
        assert not isinstance(exc.value, bgr._RetryableError)

    def test_safety_finish_reason_is_not_retryable(self):
        payload = {"candidates": [{"content": {"parts": []}, "finishReason": "IMAGE_SAFETY"}]}
        with pytest.raises(ProductPhotoError) as exc:
            extract_image(payload)
        assert not isinstance(exc.value, bgr._RetryableError)
        assert "IMAGE_SAFETY" in str(exc.value)

    def test_text_only_answer_is_retryable(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "Sure!"}]}}]}
        with pytest.raises(bgr._RetryableError):
            extract_image(payload)


class TestGeminiProvider:
    def test_request_shape(self):
        recorder = _Recorder((200, _image_response()))
        result = _provider(recorder).generate(
            Image.new("RGB", (3000, 2000), GREEN), hint="red shirt"
        )

        assert result.mode == "RGBA"
        assert result.getpixel((1, 1))[:3] == RED
        request = recorder.requests[0]
        assert request.url.path == "/v1beta/models/gemini-3.1-flash-image-preview:generateContent"
        assert request.headers["x-goog-api-key"] == "secret"
        assert "key=" not in str(request.url)  # never in the URL, which gets logged
        body = recorder.body()
        text, image, close_up = body["contents"][0]["parts"]
        assert text["text"].endswith("\nred shirt")
        assert "second image is a close-up" in text["text"]
        assert image["inlineData"]["mimeType"] == "image/jpeg"
        sent = Image.open(BytesIO(base64.b64decode(image["inlineData"]["data"])))
        assert max(sent.size) == bgr.INPUT_MAX_PX
        crop = Image.open(BytesIO(base64.b64decode(close_up["inlineData"]["data"])))
        # 30% of the 2000px short side, at the camera's own resolution.
        assert crop.size == (600, 600)
        assert body["generationConfig"] == {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": "1:1", "imageSize": "1K"},
        }

    def test_transient_errors_are_retried(self):
        recorder = _Recorder(
            (503, {"error": {"message": "overloaded"}}),
            (429, {"error": {"message": "slow down"}}),
            (200, _image_response()),
        )
        _provider(recorder).generate(Image.new("RGB", (10, 10)))
        assert len(recorder.requests) == 3

    def test_timeouts_are_retried(self):
        recorder = _Recorder(httpx.ReadTimeout("slow"), (200, _image_response()))
        _provider(recorder).generate(Image.new("RGB", (10, 10)))
        assert len(recorder.requests) == 2

    def test_gives_up_after_three_attempts(self):
        recorder = _Recorder((503, {"error": {"message": "overloaded"}}))
        with pytest.raises(ProductPhotoError, match="overloaded"):
            _provider(recorder).generate(Image.new("RGB", (10, 10)))
        assert len(recorder.requests) == bgr.MAX_ATTEMPTS

    def test_bad_key_fails_at_once_with_a_billing_hint(self):
        recorder = _Recorder((403, {"error": {"message": "API key not valid"}}))
        with pytest.raises(ProductPhotoError, match="billing"):
            _provider(recorder).generate(Image.new("RGB", (10, 10)))
        assert len(recorder.requests) == 1

    def test_unknown_model_falls_back_and_remembers(self):
        recorder = _Recorder(
            (404, {"error": {"message": "models/x is not found"}}),
            (200, _image_response()),
            (200, _image_response()),
        )
        provider = _provider(recorder, model="gemini-made-up")
        provider.generate(Image.new("RGB", (10, 10)))
        provider.generate(Image.new("RGB", (10, 10)))

        paths = [r.url.path for r in recorder.requests]
        assert paths[0].endswith("/gemini-made-up:generateContent")
        assert paths[1].endswith(f"/{bgr.FALLBACK_PHOTO_MODELS[0]}:generateContent")
        assert paths[2] == paths[1]

    def test_every_model_unknown_is_an_error(self):
        recorder = _Recorder((404, {"error": {"message": "not found"}}))
        with pytest.raises(ProductPhotoError, match="not available"):
            _provider(recorder).generate(Image.new("RGB", (10, 10)))
        assert len(recorder.requests) == 1 + len(bgr.FALLBACK_PHOTO_MODELS) - 1

    def test_rejected_image_config_is_simplified(self):
        recorder = _Recorder(
            (400, {"error": {"message": "Unknown name \"imageSize\" at 'generation_config'"}}),
            (400, {"error": {"message": "image_config is not supported"}}),
            (200, _image_response()),
        )
        _provider(recorder).generate(Image.new("RGB", (10, 10)))
        configs = [recorder.body(i)["generationConfig"] for i in range(3)]
        assert configs[0]["imageConfig"] == {"aspectRatio": "1:1", "imageSize": "1K"}
        assert configs[1]["imageConfig"] == {"aspectRatio": "1:1"}
        assert "imageConfig" not in configs[2]

    def test_other_bad_requests_are_not_retried(self):
        recorder = _Recorder((400, {"error": {"message": "Request contains an invalid argument."}}))
        with pytest.raises(ProductPhotoError, match="400"):
            _provider(recorder).generate(Image.new("RGB", (10, 10)))
        assert len(recorder.requests) == 1

    def test_stays_inside_its_time_budget(self):
        recorder = _Recorder((503, {"error": {"message": "overloaded"}}))
        with pytest.raises(ProductPhotoError):
            _provider(recorder, total_budget=1).generate(Image.new("RGB", (10, 10)))
        assert recorder.requests == []

    def test_remove_is_the_white_background_redraw(self):
        recorder = _Recorder((200, _image_response()))
        _provider(recorder).remove(Image.new("RGB", (10, 10)))
        assert "pure white" in recorder.body()["contents"][0]["parts"][0]["text"]


class TestGetProvider:
    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(bgr, "_provider", None)

    def test_gemini_uses_the_ai_key_by_default(self, monkeypatch):
        monkeypatch.setattr(bgr, "get_settings", lambda: GEMINI)
        provider = bgr.get_provider()
        assert isinstance(provider, GeminiProductPhotoProvider)
        assert provider.api_key == "test-key"
        assert provider.total_budget == GEMINI.image_job_timeout - 30

    def test_dedicated_key_wins(self, monkeypatch):
        settings = Settings(bg_removal_provider="gemini", ai_api_key="a", product_photo_api_key="b")
        monkeypatch.setattr(bgr, "get_settings", lambda: settings)
        assert bgr.get_provider().api_key == "b"

    def test_missing_key_is_a_config_error(self, monkeypatch):
        monkeypatch.setattr(bgr, "get_settings", lambda: Settings(bg_removal_provider="gemini"))
        with pytest.raises(ValueError, match="API key"):
            bgr.get_provider()


class _FakeRedraw(GeminiProductPhotoProvider):
    """Records which image it was given and paints a solid colour."""

    def __init__(self, color=RED, fail=False):
        super().__init__(api_key="k", model="m")
        self.color = color
        self.fail = fail
        self.seen: list[tuple] = []
        self.hints: list[str | None] = []

    def generate(self, image, bg_color=(255, 255, 255), hint=None):
        self.seen.append(image.getpixel((image.width // 2, image.height // 2)))
        self.hints.append(hint)
        if self.fail:
            raise ProductPhotoError("nope")
        return Image.new("RGBA", (256, 256), (*self.color, 255))


async def _make_item(db_session: AsyncSession, user: User, **overrides) -> ClothingItem:
    paths = await ImageService().process_and_store(
        user_id=user.id, image_data=_jpeg_bytes(), original_filename="test.jpg"
    )
    defaults = {
        "user_id": user.id,
        "type": "blazer",
        "primary_color": "navy",
        "material": "wool",
        "image_path": paths["image_path"],
        "medium_path": paths["medium_path"],
        "thumbnail_path": paths["thumbnail_path"],
        "image_hash": paths["image_hash"],
        "status": ItemStatus.ready,
    }
    defaults.update(overrides)
    item = ClothingItem(**defaults)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


async def _get(db_session: AsyncSession, item_id) -> ClothingItem:
    db_session.expire_all()
    result = await db_session.execute(select(ClothingItem).where(ClothingItem.id == item_id))
    return result.scalar_one()


def _center(path) -> tuple:
    image = Image.open(path).convert("RGB")
    return image.getpixel((image.width // 2, image.height // 2))


def _near(a, b, tolerance=12) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=False))


class TestImageServiceSource:
    @pytest.mark.asyncio
    async def test_redraw_always_starts_from_the_taken_photo(self, db_session, test_user):
        item = await _make_item(db_session, test_user)
        service = ImageService()
        first = _FakeRedraw(RED)
        with patch("app.services.background_removal.get_provider", return_value=first):
            out = service.remove_background(item.image_path)
        assert _near(_center(service.storage_path / item.image_path), RED)
        assert _near(_center(service.storage_path / out["original_backup_path"]), GREEN)

        second = _FakeRedraw((20, 20, 220))
        with patch("app.services.background_removal.get_provider", return_value=second):
            service.remove_background(item.image_path, hint="navy blazer")
        # Fed the green original, not the red first redraw.
        assert _near(second.seen[0], GREEN)
        assert second.hints == ["navy blazer"]
        assert _near(_center(service.storage_path / out["original_backup_path"]), GREEN)

    @pytest.mark.asyncio
    async def test_failed_redraw_leaves_no_backup_and_no_change(self, db_session, test_user):
        item = await _make_item(db_session, test_user)
        service = ImageService()
        backup = service.storage_path / (item.image_path.rsplit(".", 1)[0] + "_orig.jpg")
        with (
            patch(
                "app.services.background_removal.get_provider", return_value=_FakeRedraw(fail=True)
            ),
            pytest.raises(ProductPhotoError),
        ):
            service.remove_background(item.image_path)
        assert not backup.exists()
        assert _near(_center(service.storage_path / item.image_path), GREEN)

    @pytest.mark.asyncio
    async def test_cutout_provider_still_works_on_the_current_image(self, db_session, test_user):
        item = await _make_item(db_session, test_user)
        service = ImageService()
        cutout = MagicMock()
        cutout.uses_original_source = False
        cutout.remove.side_effect = lambda img: Image.new("RGBA", img.size, (*RED, 255))
        with patch("app.services.background_removal.get_provider", return_value=cutout):
            service.remove_background(item.image_path)
            service.remove_background(item.image_path)
        second_input = cutout.remove.call_args_list[1].args[0]
        assert _near(second_input.getpixel((5, 5)), RED)

    def test_analysis_source_prefers_the_backup(self, tmp_path):
        service = ImageService()
        service.storage_path = tmp_path
        (tmp_path / "a.jpg").write_bytes(b"x")
        assert service.analysis_source_path("a.jpg", None) == tmp_path / "a.jpg"
        assert service.analysis_source_path("a.jpg", "a_orig.jpg") == tmp_path / "a.jpg"
        (tmp_path / "a_orig.jpg").write_bytes(b"x")
        assert service.analysis_source_path("a.jpg", "a_orig.jpg") == tmp_path / "a_orig.jpg"


class TestFabricCloseUp:
    def test_small_photos_get_none(self):
        assert bgr.fabric_close_up(Image.new("RGB", (800, 1200))) is None

    def test_crop_is_centred_and_sized(self):
        image = Image.new("RGB", (2000, 3000), GREEN)
        image.paste(RED, (900, 1400, 1100, 1600))
        crop = bgr.fabric_close_up(image)
        assert crop.size == (600, 600)
        assert crop.getpixel((300, 300)) == RED
        assert crop.getpixel((5, 5)) == GREEN

    def test_small_photo_sends_no_close_up(self):
        recorder = _Recorder((200, _image_response()))
        _provider(recorder).generate(Image.new("RGB", (400, 600)))
        parts = recorder.body()["contents"][0]["parts"]
        assert len(parts) == 2
        assert "second image" not in parts[0]["text"]


class TestHint:
    def test_describes_the_item(self):
        item = ClothingItem(
            type="shoes",
            subtype="sneakers",
            primary_color="white",
            pattern="solid",
            material="leather",
        )
        assert bg_worker.product_photo_hint(item) == (
            "- Item: sneakers\n- Material: leather\n- Pattern: solid\n- Colours: white"
        )

    def test_adds_all_colours_and_the_description(self):
        item = ClothingItem(
            type="shirt",
            primary_color="burgundy",
            colors=["burgundy", "dark_red"],
            ai_description="  Dit is een bordeauxrode\n overhemd met opstaande kraag. ",
        )
        assert bg_worker.product_photo_hint(item) == (
            "- Item: shirt\n- Colours: burgundy, dark red\n"
            "- Description: Dit is een bordeauxrode overhemd met opstaande kraag."
        )

    def test_long_description_is_cut(self):
        item = ClothingItem(type="shirt", ai_description="x" * 1000)
        line = bg_worker.product_photo_hint(item).splitlines()[-1]
        assert len(line) == len("- Description: ") + bg_worker.HINT_DESCRIPTION_MAX

    def test_nothing_known_means_no_hint(self):
        assert bg_worker.product_photo_hint(ClothingItem(type="unknown")) is None


def _worker_db(db_session):
    return (
        patch("app.workers.background_removal.get_db_session", return_value=db_session),
        patch.object(db_session, "close", new_callable=AsyncMock),
    )


class TestImageJobAutoMode:
    @pytest.mark.asyncio
    async def test_success_keeps_the_tagging_start(self, db_session, test_user):
        started = datetime.now(UTC) - timedelta(seconds=40)
        item = await _make_item(
            db_session,
            test_user,
            status=ItemStatus.processing,
            processing_kind=ProcessingKind.background_removal,
        )
        fake = _FakeRedraw()
        db_patch, close_patch = _worker_db(db_session)
        with (
            db_patch,
            close_patch,
            patch("app.services.background_removal.get_provider", return_value=fake),
        ):
            result = await bg_worker.remove_item_background_job(
                {}, str(item.id), "#FFFFFF", True, started.isoformat()
            )

        assert result["status"] == "success"
        refreshed = await _get(db_session, item.id)
        assert refreshed.status == ItemStatus.ready
        assert refreshed.processing_kind is None
        assert refreshed.original_image_path is not None
        assert refreshed.ai_started_at == started
        assert fake.hints == ["- Item: blazer\n- Material: wool\n- Colours: navy"]

    @pytest.mark.asyncio
    async def test_auto_failure_leaves_the_item_usable(self, db_session, test_user):
        started = datetime.now(UTC) - timedelta(seconds=40)
        item = await _make_item(
            db_session,
            test_user,
            status=ItemStatus.processing,
            processing_kind=ProcessingKind.background_removal,
        )
        db_patch, close_patch = _worker_db(db_session)
        with (
            db_patch,
            close_patch,
            patch(
                "app.services.background_removal.get_provider", return_value=_FakeRedraw(fail=True)
            ),
        ):
            result = await bg_worker.remove_item_background_job(
                {}, str(item.id), "#FFFFFF", True, started.isoformat()
            )

        assert result["status"] == "skipped"
        refreshed = await _get(db_session, item.id)
        assert refreshed.status == ItemStatus.ready
        assert refreshed.processing_kind is None
        assert refreshed.original_image_path is None
        assert refreshed.ai_started_at == started

    @pytest.mark.asyncio
    async def test_manual_failure_is_still_an_error(self, db_session, test_user):
        item = await _make_item(
            db_session,
            test_user,
            status=ItemStatus.processing,
            processing_kind=ProcessingKind.background_removal,
        )
        db_patch, close_patch = _worker_db(db_session)
        with (
            db_patch,
            close_patch,
            patch(
                "app.services.background_removal.get_provider", return_value=_FakeRedraw(fail=True)
            ),
        ):
            result = await bg_worker.remove_item_background_job({}, str(item.id), "#FFFFFF")

        assert result["status"] == "error"
        refreshed = await _get(db_session, item.id)
        assert refreshed.status == ItemStatus.error
        assert refreshed.processing_kind == ProcessingKind.background_removal


def _redis(job_id="photo-1"):
    redis = AsyncMock()
    redis.enqueue_job.return_value = MagicMock(job_id=job_id)
    return redis


class TestQueueAfterTagging:
    @pytest.mark.asyncio
    async def test_queues_a_ready_item(self, db_session, test_user):
        started = datetime.now(UTC) - timedelta(seconds=12)
        item = await _make_item(db_session, test_user, ai_started_at=started)
        redis = _redis()
        db_patch, close_patch = _worker_db(db_session)
        with db_patch, close_patch, patch.object(bg_worker, "get_settings", lambda: GEMINI):
            queued = await bg_worker.queue_product_photo_after_tagging(
                {"redis": redis}, str(item.id)
            )

        assert queued is True
        args = redis.enqueue_job.call_args
        assert args.args == (
            "remove_item_background_job",
            str(item.id),
            "#FFFFFF",
            True,
            started.isoformat(),
        )
        assert args.kwargs == {"_queue_name": IMAGE_QUEUE}
        refreshed = await _get(db_session, item.id)
        assert refreshed.status == ItemStatus.processing
        assert refreshed.processing_kind == ProcessingKind.background_removal
        assert refreshed.ai_job_id == "photo-1"
        assert refreshed.ai_started_at is None

    @pytest.mark.asyncio
    async def test_disabled_without_gemini(self, db_session, test_user):
        item = await _make_item(db_session, test_user)
        redis = _redis()
        assert (
            await bg_worker.queue_product_photo_after_tagging({"redis": redis}, str(item.id))
            is False
        )
        redis.enqueue_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_switched_off(self, db_session, test_user):
        item = await _make_item(db_session, test_user)
        redis = _redis()
        settings = Settings(bg_removal_provider="gemini", ai_api_key="k", product_photo_auto=False)
        with patch.object(bg_worker, "get_settings", lambda: settings):
            assert (
                await bg_worker.queue_product_photo_after_tagging({"redis": redis}, str(item.id))
                is False
            )
        redis.enqueue_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_redis_in_ctx_is_safe(self, db_session, test_user):
        item = await _make_item(db_session, test_user)
        with patch.object(bg_worker, "get_settings", lambda: GEMINI):
            assert await bg_worker.queue_product_photo_after_tagging({}, str(item.id)) is False
        assert (await _get(db_session, item.id)).status == ItemStatus.ready

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides",
        [
            {"status": ItemStatus.error},
            {"status": ItemStatus.processing},
            {"original_image_path": "already/done_orig.jpg"},
        ],
    )
    async def test_skips_items_not_at_rest_or_already_done(self, db_session, test_user, overrides):
        item = await _make_item(db_session, test_user, **overrides)
        redis = _redis()
        db_patch, close_patch = _worker_db(db_session)
        with db_patch, close_patch, patch.object(bg_worker, "get_settings", lambda: GEMINI):
            assert (
                await bg_worker.queue_product_photo_after_tagging({"redis": redis}, str(item.id))
                is False
            )
        redis.enqueue_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_failure_puts_the_item_back(self, db_session, test_user):
        started = datetime.now(UTC) - timedelta(seconds=12)
        item = await _make_item(db_session, test_user, ai_started_at=started)
        redis = _redis()
        redis.enqueue_job.side_effect = ConnectionError("redis down")
        db_patch, close_patch = _worker_db(db_session)
        with db_patch, close_patch, patch.object(bg_worker, "get_settings", lambda: GEMINI):
            assert (
                await bg_worker.queue_product_photo_after_tagging({"redis": redis}, str(item.id))
                is False
            )
        refreshed = await _get(db_session, item.id)
        assert refreshed.status == ItemStatus.ready
        assert refreshed.processing_kind is None
        assert refreshed.ai_started_at == started


class TestTaggingHandsOver:
    async def _run(self, db_session, test_user, monkeypatch, **kwargs):
        item = ClothingItem(
            user_id=test_user.id,
            type="unknown",
            image_path="test/product-photo.jpg",
            status=ItemStatus.processing,
        )
        db_session.add(item)
        await db_session.commit()

        class _StubAI:
            def __init__(self, *args, **kwargs):
                pass

            async def analyze_image(self, path, locale="en"):
                return ClothingTags(
                    type="shirt", primary_color="blue", colors=["blue"], confidence=0.9
                )

        monkeypatch.setattr(tagging, "AIService", _StubAI)
        handover = AsyncMock(return_value=True)
        monkeypatch.setattr(tagging, "queue_product_photo_after_tagging", handover)
        ctx = {"redis": _redis()}
        with (
            patch("app.workers.tagging.get_db_session", return_value=db_session),
            patch.object(db_session, "close", new_callable=AsyncMock),
        ):
            result = await tagging.tag_item_image(ctx, str(item.id), __file__, **kwargs)
        return item, result, handover, ctx

    @pytest.mark.asyncio
    async def test_new_upload_hands_over_after_tagging(self, db_session, test_user, monkeypatch):
        item, result, handover, ctx = await self._run(
            db_session, test_user, monkeypatch, product_photo=True
        )
        assert result["status"] == "success"
        handover.assert_awaited_once_with(ctx, str(item.id))
        assert (await _get(db_session, item.id)).type == "shirt"

    @pytest.mark.asyncio
    async def test_reanalysis_does_not(self, db_session, test_user, monkeypatch):
        _item, result, handover, _ctx = await self._run(db_session, test_user, monkeypatch)
        assert result["status"] == "success"
        handover.assert_not_awaited()


class TestUploadAndReplace:
    @pytest.mark.asyncio
    async def test_upload_asks_tagging_for_a_product_photo(self, client: AsyncClient, auth_headers):
        with patch("app.api.items.create_pool", new_callable=AsyncMock) as create_pool:
            redis = _redis("tag-1")
            create_pool.return_value = redis
            response = await client.post(
                "/api/v1/items",
                files={"image": ("shirt.jpg", _jpeg_bytes(), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 201, response.json()
        call = redis.enqueue_job.call_args
        assert call.args[0] == "tag_item_image"
        assert call.kwargs["product_photo"] is True

    @pytest.mark.asyncio
    async def test_bulk_upload_asks_too(self, client: AsyncClient, auth_headers):
        with patch("app.api.items.create_pool", new_callable=AsyncMock) as create_pool:
            redis = _redis("tag-1")
            create_pool.return_value = redis
            response = await client.post(
                "/api/v1/items/bulk",
                files=[
                    ("images", ("a.jpg", _noise_jpeg(1), "image/jpeg")),
                    ("images", ("b.jpg", _noise_jpeg(2), "image/jpeg")),
                ],
                headers=auth_headers,
            )
        assert response.status_code == 201, response.json()
        calls = [c for c in redis.enqueue_job.call_args_list if c.args[0] == "tag_item_image"]
        assert len(calls) == 2
        assert all(c.kwargs["product_photo"] is True for c in calls)

    @pytest.mark.asyncio
    async def test_reanalysis_reads_the_original_photo(
        self, client: AsyncClient, auth_headers, db_session, test_user
    ):
        item = await _make_item(db_session, test_user, original_image_path="x/orig_orig.jpg")
        backup = ImageService().storage_path / "x" / "orig_orig.jpg"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(_jpeg_bytes())
        with patch("app.api.items.create_pool", new_callable=AsyncMock) as create_pool:
            redis = _redis("tag-2")
            create_pool.return_value = redis
            response = await client.post(f"/api/v1/items/{item.id}/analyze", headers=auth_headers)
        assert response.status_code == 200, response.json()
        call = redis.enqueue_job.call_args
        assert call.args[2] == str(backup)
        assert "product_photo" not in call.kwargs

    @pytest.mark.asyncio
    async def test_replaced_photo_gets_a_product_photo(
        self, client: AsyncClient, auth_headers, db_session, test_user
    ):
        item = await _make_item(db_session, test_user, original_image_path=None)
        with (
            patch("app.api.items.create_pool", new_callable=AsyncMock) as create_pool,
            patch.object(bg_worker, "get_settings", lambda: GEMINI),
        ):
            redis = _redis("photo-9")
            create_pool.return_value = redis
            response = await client.put(
                f"/api/v1/items/{item.id}/image",
                files={"image": ("new.jpg", _jpeg_bytes(color=(9, 9, 9)), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["status"] == "processing"
        assert data["processing_kind"] == "background_removal"
        assert redis.enqueue_job.call_args.args[0] == "remove_item_background_job"
        assert redis.enqueue_job.call_args.args[3] is True
        redis.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_replaced_photo_without_gemini_stays_ready(
        self, client: AsyncClient, auth_headers, db_session, test_user
    ):
        item = await _make_item(db_session, test_user)
        with patch("app.api.items.create_pool", new_callable=AsyncMock) as create_pool:
            response = await client.put(
                f"/api/v1/items/{item.id}/image",
                files={"image": ("new.jpg", _jpeg_bytes(color=(9, 9, 9)), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "ready"
        create_pool.assert_not_called()

    @pytest.mark.asyncio
    async def test_replace_refuses_while_an_image_job_runs(
        self, client: AsyncClient, auth_headers, db_session, test_user
    ):
        item = await _make_item(
            db_session,
            test_user,
            status=ItemStatus.processing,
            processing_kind=ProcessingKind.background_removal,
            ai_job_id="busy",
        )
        response = await client.put(
            f"/api/v1/items/{item.id}/image",
            files={"image": ("new.jpg", _jpeg_bytes(), "image/jpeg")},
            headers=auth_headers,
        )
        assert response.status_code == 409


class TestSingleEndpointErrors:
    @pytest.mark.asyncio
    async def test_product_photo_error_is_readable(
        self, client: AsyncClient, auth_headers, db_session, test_user
    ):
        item = await _make_item(db_session, test_user)
        with patch(
            "app.services.background_removal.get_provider", return_value=_FakeRedraw(fail=True)
        ):
            response = await client.post(
                f"/api/v1/items/{item.id}/remove-background",
                json={"bg_color": "#FFFFFF"},
                headers=auth_headers,
            )
        assert response.status_code == 502
        assert response.json()["detail"] == "nope"


class TestFeatures:
    @pytest.mark.asyncio
    async def test_reports_product_photo_mode(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr("app.api.health.get_provider", lambda: _FakeRedraw())
        data = (await client.get("/api/v1/health/features")).json()
        assert data["background_removal"] is True
        assert data["background_removal_mode"] == "product_photo"

    @pytest.mark.asyncio
    async def test_reports_cutout_mode(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr("app.api.health.get_provider", lambda: MagicMock())
        data = (await client.get("/api/v1/health/features")).json()
        assert data["background_removal_mode"] == "cutout"
        assert data["product_photo_auto"] is False

    @pytest.mark.asyncio
    async def test_unavailable(self, client: AsyncClient, monkeypatch):
        def boom():
            raise ValueError("no")

        monkeypatch.setattr("app.api.health.get_provider", boom)
        data = (await client.get("/api/v1/health/features")).json()
        assert data["background_removal"] is False
        assert data["background_removal_mode"] is None


class TestAnalysisBanner:
    @pytest.mark.asyncio
    async def test_item_getting_its_photo_still_counts_as_analysed(
        self, client: AsyncClient, auth_headers, db_session, test_user
    ):
        await _make_item(
            db_session, test_user, status=ItemStatus.processing, ai_processed=False, type="unknown"
        )
        # Tagged after the run's anchor (the waiting item's created_at).
        now = datetime.now(UTC) + timedelta(seconds=5)
        await _make_item(
            db_session,
            test_user,
            status=ItemStatus.processing,
            processing_kind=ProcessingKind.background_removal,
            ai_processed=True,
            ai_completed_at=now,
        )
        data = (await client.get("/api/v1/items/tagging-progress", headers=auth_headers)).json()
        assert data["processing"] == 1
        assert data["batch_completed"] == 1
        assert data["batch_total"] == 2
