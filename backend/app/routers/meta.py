"""Application metadata."""
from fastapi import APIRouter, Depends

from app.config import get_settings
from app.permissions import ALL_PERMISSIONS

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("")
def meta():
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "local_timezone": settings.local_timezone,
        "permissions": ALL_PERMISSIONS,
    }
