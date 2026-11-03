import base64
import logging
import threading
import time
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageOps

from app.config import get_settings

logger = logging.getLogger(__name__)


class BackgroundRemovalProvider(ABC):
    # True when the provider must always see the untouched photo. A redraw of a redraw
    # drifts further from the real garment on every run, so ImageService feeds such a
    # provider the _orig backup once one exists instead of the current image.
    uses_original_source = False

    @abstractmethod
    def remove(self, image: Image.Image) -> Image.Image:
        """Remove background from image. Returns RGBA image with transparent background."""


# rembg is imported lazily because it is an optional extra (requirements-extras.txt): a
# module-level import would break every install that uses the http provider or none.
class RembgProvider(BackgroundRemovalProvider):
    def __init__(self, model: str = "u2net"):
        self.model = model
        self._session = None

    def _get_session(self):
        if self._session is None:
            from rembg import new_session

            self._session = new_session(self.model)
        return self._session

    def remove(self, image: Image.Image) -> Image.Image:
        from rembg import remove

        return remove(image, session=self._get_session())


class HttpProvider(BackgroundRemovalProvider):
    def __init__(self, url: str, api_key: str | None = None):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def remove(self, image: Image.Image) -> Image.Image:
        buf = BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(timeout=120, follow_redirects=True) as client:
            response = client.post(
                f"{self.url}/api/remove-background",
                files={"file": ("image.png", buf, "image/png")},
                headers=headers,
            )
            response.raise_for_status()

        return Image.open(BytesIO(response.content)).convert("RGBA")


class ProductPhotoError(Exception):
    """A product photo could not be made. The message is safe to show and to log."""


class _RetryableError(ProductPhotoError):
    pass


class _ModelNotFoundError(ProductPhotoError):
    pass


class _ImageConfigRejectedError(ProductPhotoError):
    pass


# Tried in this order after the configured model answers 404, because Google renames image
# models between preview and GA and retires the old id some months later.
FALLBACK_PHOTO_MODELS = (
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
)

# finishReason values that mean "this photo was refused" rather than "try again".
_BLOCKED_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "IMAGE_SAFETY",
        "PROHIBITED_CONTENT",
        "IMAGE_PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "RECITATION",
        "IMAGE_RECITATION",
    }
)

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
RETRY_BACKOFF_SECONDS = (5, 15)
MAX_ATTEMPTS = 3
# Long edge of the photo sent to Gemini. The model works at about 1K anyway, so a 12MP
# phone photo would only cost upload time.
INPUT_MAX_PX = 1536
# Below this much time left, a new attempt would only be killed by the deadline.
MIN_ATTEMPT_SECONDS = 15

PRODUCT_PHOTO_PROMPT = """\
Turn this photo into a clean, professional e-commerce product photo of the clothing item in it.

Keep the item exactly as it is: the same garment, the same colours and shades, the same \
pattern, print, texture and fabric, the same logos, labels, text, buttons, zips, pockets, \
seams and details, the same cut, length and proportions. Do not add, remove, redesign or \
recolour anything on the item, and do not invent details that are not visible.

Make it look brand new: neatly pressed and ironed, wrinkle-free, smooth and symmetrical, \
laid out or shaped the way a web shop would show it. Clothes as a neat flat lay seen from \
straight above, or on an invisible ghost mannequin when that suits the garment better; \
shoes, bags and accessories as a product shot at a slight angle.

Show only the item: no person, no hands, no hanger, no mannequin, no props, no other \
clothes, no text, no watermark. Centre the whole item in the frame with a small even margin \
and do not crop any part of it.

Background: a plain, seamless, solid {background} background filling the whole image, with \
soft, even studio lighting and only a very subtle natural shadow under the item.\
"""


def _background_phrase(bg_color: tuple[int, int, int]) -> str:
    hex_color = "#{:02X}{:02X}{:02X}".format(*bg_color)
    if bg_color == (255, 255, 255):
        return f"pure white ({hex_color})"
    return f"colour {hex_color}"


def build_prompt(bg_color: tuple[int, int, int], hint: str | None = None) -> str:
    prompt = PRODUCT_PHOTO_PROMPT.format(background=_background_phrase(bg_color))
    if hint:
        prompt += f"\n\nThe item is: {hint}."
    return prompt


def _encode_input(image: Image.Image) -> str:
    image = ImageOps.exif_transpose(image) or image
    if image.mode != "RGB":
        image = image.convert("RGB")
    image = image.copy()
    image.thumbnail((INPUT_MAX_PX, INPUT_MAX_PX), Image.Resampling.LANCZOS)
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _google_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or response.reason_phrase
    if isinstance(body, list) and body:
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    return str(body)[:300]


def extract_image(payload: dict[str, Any]) -> Image.Image:
    """Return the final image of a generateContent response, or raise ProductPhotoError."""
    feedback = payload.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise ProductPhotoError(f"Gemini refused this photo ({feedback['blockReason']})")

    candidates = payload.get("candidates") or []
    finish_reason = None
    for candidate in candidates:
        finish_reason = candidate.get("finishReason") or finish_reason
        parts = (candidate.get("content") or {}).get("parts") or []
        # Thinking models can stream draft images as thought parts before the answer;
        # only the last non-thought image is the result.
        images = [
            part["inlineData"]
            for part in parts
            if isinstance(part, dict)
            and not part.get("thought")
            and isinstance(part.get("inlineData"), dict)
            and part["inlineData"].get("data")
        ]
        if images:
            data = base64.b64decode(images[-1]["data"])
            try:
                image = Image.open(BytesIO(data))
                image.load()
            except Exception as e:
                raise _RetryableError(f"Gemini returned an unreadable image: {e}") from None
            return image.convert("RGBA")

    if finish_reason in _BLOCKED_FINISH_REASONS:
        raise ProductPhotoError(f"Gemini refused this photo ({finish_reason})")
    # Text only, or nothing at all: image models occasionally answer in words instead.
    raise _RetryableError(f"Gemini returned no image (finishReason={finish_reason or 'none'})")


class GeminiProductPhotoProvider(BackgroundRemovalProvider):
    """Redraws the item as a pressed, catalogue-style photo with a Gemini image model.

    Unlike cut-out providers this repaints the whole picture, so the result is already
    opaque and remove() returns it with a fully opaque alpha channel.
    """

    uses_original_source = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        image_size: str | None = "1K",
        aspect_ratio: str | None = "1:1",
        request_timeout: float = 90,
        total_budget: float = 270,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError(
                "A Gemini API key is required for product photos "
                "(set PRODUCT_PHOTO_API_KEY or AI_API_KEY)"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.image_size = image_size or None
        self.aspect_ratio = aspect_ratio or None
        self.request_timeout = request_timeout
        self.total_budget = total_budget
        self._transport = transport  # tests inject an httpx.MockTransport
        self._models = [model, *(m for m in FALLBACK_PHOTO_MODELS if m != model)]
        # Learned once per process: which model id answers, and whether it takes imageConfig.
        self._model_index = 0
        self._image_config_level = 2  # 2 = size + ratio, 1 = ratio only, 0 = none
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._models[self._model_index]

    def remove(self, image: Image.Image) -> Image.Image:
        return self.generate(image)

    def generate(
        self,
        image: Image.Image,
        bg_color: tuple[int, int, int] = (255, 255, 255),
        hint: str | None = None,
    ) -> Image.Image:
        deadline = time.monotonic() + self.total_budget
        body_parts = [
            {"text": build_prompt(bg_color, hint)},
            {"inlineData": {"mimeType": "image/jpeg", "data": _encode_input(image)}},
        ]

        attempt = 0
        last_error: ProductPhotoError | None = None
        with httpx.Client(follow_redirects=True, transport=self._transport) as client:
            while True:
                remaining = deadline - time.monotonic()
                if remaining < MIN_ATTEMPT_SECONDS:
                    break
                try:
                    payload = self._post(client, body_parts, min(self.request_timeout, remaining))
                    result = extract_image(payload)
                    logger.info("Product photo made with %s", self.model)
                    return result
                except _ModelNotFoundError as e:
                    if not self._next_model():
                        raise ProductPhotoError(str(e)) from None
                    logger.warning("%s; trying %s", e, self.model)
                    continue  # a different model is not a retry of the same request
                except _ImageConfigRejectedError as e:
                    if not self._drop_image_config():
                        raise ProductPhotoError(str(e)) from None
                    logger.warning("%s; retrying with a simpler imageConfig", e)
                    continue
                except _RetryableError as e:
                    last_error = e
                    attempt += 1
                    if attempt >= MAX_ATTEMPTS:
                        break
                    delay = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                    if deadline - time.monotonic() - delay < MIN_ATTEMPT_SECONDS:
                        break
                    logger.warning(
                        "Product photo attempt %d failed (%s); retrying in %ds", attempt, e, delay
                    )
                    time.sleep(delay)

        if last_error is not None:
            raise ProductPhotoError(str(last_error)) from None
        raise ProductPhotoError("Product photo timed out")

    def _next_model(self) -> bool:
        with self._lock:
            if self._model_index + 1 >= len(self._models):
                return False
            self._model_index += 1
            self._image_config_level = 2
            return True

    def _drop_image_config(self) -> bool:
        with self._lock:
            if self._image_config_level == 0:
                return False
            self._image_config_level -= 1
            return True

    def _generation_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config: dict[str, str] = {}
        if self._image_config_level >= 1 and self.aspect_ratio:
            image_config["aspectRatio"] = self.aspect_ratio
        if self._image_config_level >= 2 and self.image_size:
            image_config["imageSize"] = self.image_size
        if image_config:
            config["imageConfig"] = image_config
        return config

    def _post(
        self, client: httpx.Client, parts: list[dict[str, Any]], timeout: float
    ) -> dict[str, Any]:
        model = self.model
        url = f"{self.base_url}/models/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": self._generation_config(),
        }
        try:
            response = client.post(
                url,
                json=body,
                headers={"x-goog-api-key": self.api_key},
                timeout=httpx.Timeout(timeout, connect=min(15.0, timeout)),
            )
        except httpx.TimeoutException:
            raise _RetryableError(f"Gemini did not answer within {int(timeout)}s") from None
        except httpx.TransportError as e:
            raise _RetryableError(f"Could not reach Gemini: {e}") from None

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise _RetryableError("Gemini sent an invalid response") from None

        message = _google_error_message(response)
        status = response.status_code
        if status == 404:
            raise _ModelNotFoundError(f"Model {model} is not available ({message})")
        if status == 400:
            lowered = message.lower().replace("_", "")
            if any(word in lowered for word in ("imageconfig", "imagesize", "aspectratio")):
                raise _ImageConfigRejectedError(f"{model} rejected imageConfig ({message})")
            if "modalit" in lowered:
                raise _ModelNotFoundError(f"Model {model} cannot make images ({message})")
        if status in (401, 403):
            raise ProductPhotoError(
                f"Gemini refused the API key ({status}): {message}. Image output needs a "
                "key from a Google AI Studio project with billing enabled."
            )
        if status in RETRYABLE_STATUS:
            raise _RetryableError(f"Gemini error {status}: {message}")
        raise ProductPhotoError(f"Gemini error {status}: {message}")


_provider: BackgroundRemovalProvider | None = None


def get_provider() -> BackgroundRemovalProvider:
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    provider_type = settings.bg_removal_provider

    if provider_type == "rembg":
        _provider = RembgProvider(model=settings.bg_removal_model)
    elif provider_type == "http":
        if not settings.bg_removal_url:
            raise ValueError("BG_REMOVAL_URL is required when BG_REMOVAL_PROVIDER=http")
        _provider = HttpProvider(url=settings.bg_removal_url, api_key=settings.bg_removal_api_key)
    elif provider_type == "gemini":
        _provider = GeminiProductPhotoProvider(
            api_key=settings.product_photo_api_key or settings.ai_api_key or "",
            model=settings.product_photo_model,
            base_url=settings.product_photo_base_url,
            image_size=settings.product_photo_image_size,
            aspect_ratio=settings.product_photo_aspect_ratio,
            request_timeout=settings.product_photo_timeout,
            # Leaves the worker time to write the images and its status before arq's
            # job_timeout, whose kill would strand the item in `processing`.
            total_budget=max(MIN_ATTEMPT_SECONDS * 2, settings.image_job_timeout - 30),
        )
    else:
        raise ValueError(
            f"Unknown BG_REMOVAL_PROVIDER: {provider_type}. Use 'rembg', 'http' or 'gemini'."
        )

    return _provider
