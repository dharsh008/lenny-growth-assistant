import re

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequest
from app.db.models import User, _now
from app.db.session import get_db

USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


async def current_user(
    request: Request,
    x_user_id: str | None = Header(default=None, description="Anonymous per-browser id (8-64 chars: letters, digits, _ -)"),
    x_user_name: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not x_user_id or not USER_ID_RE.match(x_user_id):
        raise BadRequest("Missing or invalid X-User-Id header (8-64 chars: letters, digits, '_' or '-').",
                         details={"header": "X-User-Id"})
    user = await db.get(User, x_user_id)
    ua = (request.headers.get("user-agent") or "")[:200]
    if user is None:
        user = User(id=x_user_id, display_name=(x_user_name or None), meta={"user_agent": ua, "first_ip": request.client.host if request.client else None})
        db.add(user)
    else:
        user.last_seen_at = _now()
        user.meta = {**(user.meta or {}), "user_agent": ua}
        if x_user_name:
            user.display_name = x_user_name[:120]
    await db.flush()
    request.state.user_id = user.id
    return user
