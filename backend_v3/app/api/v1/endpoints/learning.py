"""
api/v1/endpoints/learning.py

Learning from detections. Administrators only.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.permissions import require
from app.db.models.user import User
from app.db.session import get_db
from app.services import learning_service, run_lock

router = APIRouter(prefix="/learning", tags=["Learning"])


@router.get("/status")
def learning_status(db: Session = Depends(get_db), user: User = Depends(require("learning.manage"))):
    return learning_service.status(db)


@router.post("/train", status_code=status.HTTP_202_ACCEPTED)
def start_round(activate_without_validation: bool = Query(False),
                user: User = Depends(require("learning.manage"))):
    if not learning_service.request_training("admin", activate=activate_without_validation):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A detection or a learning round is already running. Try again in a moment.")
    return {"message": "Learning round started. Check the status in a few minutes."}


@router.post("/activate-candidate")
def activate_candidate(db: Session = Depends(get_db), user: User = Depends(require("learning.manage"))):
    if not run_lock.try_claim():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A detection or a learning round is running. Try again in a moment.")
    try:
        name = learning_service.activate_latest_candidate(db)
    finally:
        run_lock.release_claim()
    return {"message": f"Saved version {name} is now in use."}
