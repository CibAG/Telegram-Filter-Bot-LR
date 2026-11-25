from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.services.ai_moderation import judge_message
from app.services.matching import find_matching_filter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModerationOutcome:
    delete: bool = False
    reason: str | None = None
    filter_id: int | None = None
    rule_id: int | None = None
    pattern: str | None = None
    ai_rule_id: int | None = None
    ai_reason: str | None = None
    ai_error: str | None = None


NO_ACTION = ModerationOutcome()


async def evaluate_message(
    session: AsyncSession,
    *,
    chat_id: int,
    thread_id: int | None,
    user_id: int,
    text: str | None,
    caption: str | None,
) -> ModerationOutcome:
    body = text or ""
    filters = await repo.active_word_filters(session, chat_id, thread_id)
    matched_filter = find_matching_filter(filters, body, caption)
    if matched_filter is not None:
        return ModerationOutcome(
            delete=True,
            reason=f"word_filter:{matched_filter.id}",
            filter_id=matched_filter.id,
            pattern=matched_filter.pattern,
        )

    rules = await repo.active_rules(session, chat_id, thread_id)
    for rule in rules:
        if rule.target_user_id is not None and rule.target_user_id != user_id:
            continue
        if await repo.is_exception(session, rule.id, user_id):
            continue
        return ModerationOutcome(
            delete=True,
            reason=f"rule:{rule.id}",
            rule_id=rule.id,
        )

    ai_rule = await repo.active_ai_rule(session, chat_id, thread_id)
    body = (text or caption or "").strip()
    if ai_rule is not None and body:
        # Do not hold the SQLite transaction open during the external call.
        await session.commit()
        try:
            verdict = await judge_message(instruction=ai_rule.instruction, text=body)
        except Exception as exc:  # noqa: BLE001 - fail-open for any AI failure
            logger.warning("AI moderation rule %s failed: %s", ai_rule.id, exc)
            return ModerationOutcome(
                ai_rule_id=ai_rule.id,
                ai_error=str(exc) or type(exc).__name__,
            )
        if verdict.delete:
            return ModerationOutcome(
                delete=True,
                reason=f"ai:{ai_rule.id}",
                ai_rule_id=ai_rule.id,
                ai_reason=verdict.reason,
            )

    return NO_ACTION
