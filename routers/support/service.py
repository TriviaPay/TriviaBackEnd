"""Support service layer."""

from typing import Optional

from fastapi import HTTPException, status

from . import repository as support_repository


def list_faqs(db):
    faqs = support_repository.list_faqs(db)
    return {"faqs": faqs}


def create_faq(db, *, question: str, answer: str):
    faq = support_repository.create_faq(db, question=question, answer=answer)
    db.commit()
    db.refresh(faq)
    return faq


def update_faq(db, *, faq_id: int, question: Optional[str], answer: Optional[str]):
    faq = support_repository.get_faq(db, faq_id=faq_id)
    if not faq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAQ not found")
    if question is not None:
        faq.question = question
    if answer is not None:
        faq.answer = answer
    db.commit()
    db.refresh(faq)
    return faq


def delete_faq(db, *, faq_id: int):
    faq = support_repository.get_faq(db, faq_id=faq_id)
    if not faq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAQ not found")
    support_repository.delete_faq(db, faq=faq)
    db.commit()
    return {"deleted": True, "faq_id": faq_id}
