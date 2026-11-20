import logging
import os
import uuid
from typing import Annotated, Optional

from app.database.crud.user_crud import user as user_crud
from app.database.database import get_db
from app.schemas.user import CurrentUser
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Session cookie name
SESSION_COOKIE_NAME = "session_token"

# Setup header auth
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

AUTO_AUTH = os.getenv("AUTO_AUTH", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AUTO_AUTH_EMAIL = os.getenv("AUTO_AUTH_EMAIL", "arthur@example.com")
AUTO_AUTH_NAME = os.getenv("AUTO_AUTH_NAME", "Arthur")


def _auto_auth_user(db: Session) -> CurrentUser:
    """Return (and create if needed) the single local user for AUTO_AUTH."""
    db_user = user_crud.get_by_email_and_provider(
        db, email=AUTO_AUTH_EMAIL, provider="email"
    )
    if not db_user:
        db_user = user_crud.create_email_user(
            db, email=AUTO_AUTH_EMAIL, name=AUTO_AUTH_NAME
        )
        db_user = user_crud.verify_email(db, user=db_user)

    changed = False
    if not db_user.is_active:
        db_user.is_active = True
        changed = True
    if db_user.is_blocked:
        db_user.is_blocked = False
        changed = True
    if not db_user.is_email_verified:
        db_user.is_email_verified = True
        changed = True
    if changed:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

    return CurrentUser(
        id=uuid.UUID(str(db_user.id)),
        email=str(db_user.email),
        name=db_user.name,  # type: ignore
        is_admin=bool(db_user.is_admin),
        picture=db_user.picture,  # type: ignore
        is_email_verified=True,
        is_active=True,
        is_blocked=False,
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str = Depends(api_key_header),
) -> Optional[CurrentUser]:
    """
    Get the current user from session token in cookie or Authorization header.

    This is a FastAPI dependency that can be used in route functions.
    """
    if AUTO_AUTH:
        return _auto_auth_user(db)

    token = None

    # First try from Authorization header
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    # Then try from cookie
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)

    if not token:
        return None

    # Get session from database
    db_session = user_crud.get_by_token(db=db, token=token)
    if not db_session:
        return None

    # Get user from session
    db_user = user_crud.get(db=db, id=db_session.user_id)
    if not db_user or not db_user.is_active:
        return None

    if not db_user.id:
        logger.error("User ID is missing in the database record.")
        return None

    id_as_uuid = uuid.UUID(str(db_user.id))

    # Return CurrentUser model
    return CurrentUser(
        id=id_as_uuid,
        email=str(db_user.email),
        name=db_user.name,  # type: ignore
        is_admin=bool(db_user.is_admin),
        picture=db_user.picture,  # type: ignore
        is_email_verified=bool(db_user.is_email_verified),
        is_active=True,
        is_blocked=bool(db_user.is_blocked),
    )


async def get_required_user(
    current_user: Annotated[Optional[CurrentUser], Depends(get_current_user)]
) -> CurrentUser:
    """
    Require a logged-in user for protected routes.
    Raises 401 Unauthorized if no user is found.
    """
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


async def get_admin_user(
    current_user: Annotated[CurrentUser, Depends(get_required_user)]
) -> CurrentUser:
    """
    Require an admin user for admin-only routes.
    Raises 403 Forbidden if user is not admin.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return current_user
