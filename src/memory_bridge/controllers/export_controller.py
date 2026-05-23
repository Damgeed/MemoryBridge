"""Data export/import endpoints for tenant migration."""

import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..dependencies import get_storage
from ..repository import MemoryRepository
from ..services.export_service import ExportService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


async def get_export_service(storage: MemoryRepository = Depends(get_storage)):
    return ExportService(repo=storage)


@router.get("/export/{project}")
async def export_project_data(
    project: str,
    request: Request,
    service: ExportService = Depends(get_export_service),
):
    """Export all data for a project as JSON."""
    # Check auth — only admins can export
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    data = await service.export_project(project)
    return data


class ImportRequest(BaseModel):
    data: dict
    target_project: str


@router.post("/import")
async def import_project_data(
    payload: ImportRequest,
    request: Request,
    service: ExportService = Depends(get_export_service),
):
    """Import data into a project."""
    # Check auth
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await service.import_project(payload.data, payload.target_project)
    return result
