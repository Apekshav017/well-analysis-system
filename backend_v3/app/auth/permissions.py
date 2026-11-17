"""
auth/permissions.py

Two account profiles:
  - administrator (users.is_admin = true): every right
  - user: the rights listed in USER_PERMISSIONS

The server enforces these rights. The frontend only hides what an account cannot use.
"""

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.core.exceptions import AuthorizationException
from app.db.models.user import User

USER_PERMISSIONS = frozenset({
    "detect.own",           # run detections: by village name, an existing image, or an upload
    "jobs.abort.own",       # stop own running detections
    "results.delete.own",   # delete own runs and wells
    "export",               # CSV and server exports
})

ADMIN_PERMISSIONS = USER_PERMISSIONS | frozenset({
    "detect.advanced",      # choose confidence, tile size and colour stretch
    "jobs.abort.any",       # stop anyone's running detection
    "results.delete.any",   # delete anyone's results
    "wells.manual",         # add or remove wells the detection missed
    "settings.manage",      # thresholds, detection defaults, learning settings
    "learning.manage",      # start learning rounds, activate saved versions
})


def permissions_for(user: User) -> frozenset[str]:
    return ADMIN_PERMISSIONS if user.is_admin else USER_PERMISSIONS


def role_of(user: User) -> str:
    return "admin" if user.is_admin else "user"


def can(user: User, permission: str) -> bool:
    return permission in permissions_for(user)


def require(permission: str):
    """FastAPI dependency: the signed-in user, or 403 when the account lacks the permission."""

    def _check(user: User = Depends(get_current_user)) -> User:
        if not can(user, permission):
            raise AuthorizationException("Your account does not have permission for this action.")
        return user

    return _check
