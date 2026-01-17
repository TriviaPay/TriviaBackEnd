from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from models import User
from routers.dependencies import get_current_user, verify_admin

from .schemas import (
    FAQCreateRequest,
    FAQDeleteResponse,
    FAQListResponse,
    FAQResponse,
    FAQUpdateRequest,
)
from .service import (
    create_faq as service_create_faq,
    delete_faq as service_delete_faq,
    list_faqs as service_list_faqs,
    update_faq as service_update_faq,
)

router = APIRouter()


@router.get("/faqs", response_model=FAQListResponse, tags=["Support"])
def list_faqs(db: Session = Depends(get_db)):
    return service_list_faqs(db)


@router.post("/admin/faqs", response_model=FAQResponse, tags=["Admin"])
def create_faq(
    payload: FAQCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_admin(db, current_user)
    return service_create_faq(db, question=payload.question, answer=payload.answer)


@router.put("/admin/faqs/{faq_id}", response_model=FAQResponse, tags=["Admin"])
def update_faq(
    faq_id: int,
    payload: FAQUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_admin(db, current_user)
    return service_update_faq(
        db, faq_id=faq_id, question=payload.question, answer=payload.answer
    )


@router.delete("/admin/faqs/{faq_id}", response_model=FAQDeleteResponse, tags=["Admin"])
def delete_faq(
    faq_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_admin(db, current_user)
    return service_delete_faq(db, faq_id=faq_id)
