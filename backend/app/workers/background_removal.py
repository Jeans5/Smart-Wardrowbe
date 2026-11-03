import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.config import get_settings
from app.models.item import ClothingItem, ItemStatus, ProcessingKind
from app.services.image_service import ImageService
from app.workers.db import get_db_session
from app.workers.queues import IMAGE_QUEUE

logger = logging.getLogger(__name__)

PRODUCT_PHOTO_BG_COLOR = "#FFFFFF"
HINT_DESCRIPTION_MAX = 400


def _label(value: str | None) -> str | None:
    if not value or value == "unknown":
        return None
    return value.replace("_", " ").replace("-", " ")


def product_photo_hint(item: ClothingItem) -> str | None:
    """What tagging already saw of the item, to steer the redraw - above all its fabric.

    Only facts from the analysis or the person's own edits, so it can tell the model
    "this is cotton, these are shoes" without inventing anything the photo does not show.
    """
    kind = _label(item.subtype) or _label(item.type)
    if not kind:
        return None
    lines = [f"- Item: {kind}"]
    if material := _label(item.material):
        lines.append(f"- Material: {material}")
    if pattern := _label(item.pattern):
        lines.append(f"- Pattern: {pattern}")
    colours = [_label(c) for c in [item.primary_color, *(item.colors or [])]]
    colours = list(dict.fromkeys(c for c in colours if c))
    if colours:
        lines.append(f"- Colours: {', '.join(colours)}")
    if item.ai_description:
        description = " ".join(item.ai_description.split())
        if len(description) > HINT_DESCRIPTION_MAX:
            description = description[: HINT_DESCRIPTION_MAX - 1].rstrip() + "…"
        lines.append(f"- Description: {description}")
    return "\n".join(lines)


def _parse_started_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def remove_item_background_job(
    ctx: dict,
    item_id: str,
    bg_color_hex: str,
    auto: bool = False,
    restore_started_at: str | None = None,
) -> dict:
    """Remove the background for one item and composite it onto bg_color_hex.

    With BG_REMOVAL_PROVIDER=gemini this redraws the item as a product photo instead.

    No retry/cooldown machinery is needed here - unlike tag_item_image - because the
    Gemini provider retries transient errors itself. A failure is marked as an error
    status, and the user retries via the bulk toolbar. An automatic run (auto=True,
    queued for a new photo) is the exception: the item is already tagged and usable,
    so a failed product photo just leaves it ready with the photo as it was taken.
    """
    hex_color = bg_color_hex.lstrip("#")
    bg_color = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    # The tagging attempt's own start time, put back afterwards so the analysis
    # banner keeps measuring how long tagging took.
    kept_started_at = _parse_started_at(restore_started_at)

    db = get_db_session(ctx)
    try:
        result = await db.execute(select(ClothingItem).where(ClothingItem.id == UUID(item_id)))
        item = result.scalar_one_or_none()
        if item is None:
            logger.warning(f"Item {item_id} not found for background removal job")
            return {"status": "error", "error": "Item not found"}

        item.ai_started_at = datetime.now(UTC)
        await db.commit()

        try:
            image_service = ImageService()
            out = await asyncio.to_thread(
                image_service.remove_background,
                item.image_path,
                bg_color,
                product_photo_hint(item),
            )
            item.original_image_path = out["original_backup_path"]
            item.status = ItemStatus.ready
            item.ai_started_at = kept_started_at
            item.processing_kind = None
            await db.commit()
            return {"status": "success", "item_id": item_id}
        except Exception as e:
            if auto:
                logger.warning(f"Automatic product photo failed for item {item_id}: {e}")
                item.status = ItemStatus.ready
                item.processing_kind = None
                item.ai_started_at = kept_started_at
                await db.commit()
                return {"status": "skipped", "error": str(e)}
            logger.error(f"Background removal failed for item {item_id}: {e}")
            # Kind stays "background_removal" so the grid labels and retries
            # this as a background-removal failure, not a generic AI failure -
            # and ai_raw_response/ai_failed_at are left untouched since this
            # job never touched AI tagging's own failure bookkeeping.
            item.status = ItemStatus.error
            item.ai_started_at = None
            await db.commit()
            return {"status": "error", "error": str(e)}
    finally:
        await db.close()


def wants_auto_product_photo(item: ClothingItem) -> bool:
    settings = get_settings()
    # original_image_path set means this photo was already processed once; the user
    # may have chosen it, and paying for a second redraw would surprise them.
    return bool(
        settings.product_photo_auto_enabled and item.image_path and not item.original_image_path
    )


async def enqueue_product_photo(redis: Any, db: Any, item: ClothingItem) -> bool:
    """Hand a ready item to the image worker for its automatic product photo.

    Follows the commit-status-then-enqueue-then-set-job-id order every enqueue site
    uses, so the stale sweep can always account for the row. Returns whether a job
    was queued; on any failure the item is left ready, never stuck in `processing`.
    """
    if redis is None or not wants_auto_product_photo(item):
        return False

    kept_started_at = item.ai_started_at.isoformat() if item.ai_started_at else None
    item.status = ItemStatus.processing
    item.processing_kind = ProcessingKind.background_removal
    item.ai_started_at = None
    await db.commit()

    try:
        job = await redis.enqueue_job(
            "remove_item_background_job",
            str(item.id),
            PRODUCT_PHOTO_BG_COLOR,
            True,
            kept_started_at,
            _queue_name=IMAGE_QUEUE,
        )
        if job is None:
            raise RuntimeError("enqueue_job returned None")
    except Exception as e:
        logger.error(f"Failed to queue product photo for item {item.id}: {e}")
        item.status = ItemStatus.ready
        item.processing_kind = None
        item.ai_started_at = _parse_started_at(kept_started_at)
        await db.commit()
        return False

    item.ai_job_id = job.job_id
    await db.commit()
    logger.info(f"Queued product photo for item {item.id}")
    return True


async def queue_product_photo_after_tagging(ctx: dict, item_id: str) -> bool:
    """Called by the tagging worker once a new upload has been tagged."""
    if not get_settings().product_photo_auto_enabled:
        return False
    redis = ctx.get("redis")
    if redis is None:
        logger.warning(f"No Redis in worker context; skipping product photo for item {item_id}")
        return False

    db = get_db_session(ctx)
    try:
        result = await db.execute(
            select(ClothingItem).where(ClothingItem.id == UUID(item_id)).with_for_update()
        )
        item = result.scalar_one_or_none()
        # Only straight from a successful tagging run: anything else means the user or
        # another job moved the item on in the meantime.
        if item is None or item.status != ItemStatus.ready or item.processing_kind is not None:
            await db.rollback()
            return False
        return await enqueue_product_photo(redis, db, item)
    except Exception as e:
        logger.error(f"Could not queue product photo for item {item_id}: {e}")
        await db.rollback()
        return False
    finally:
        await db.close()
