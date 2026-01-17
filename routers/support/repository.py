"""Support repository layer."""

from sqlalchemy.orm import Session


def list_faqs(db: Session):
    from models import FAQ

    return db.query(FAQ).order_by(FAQ.id.asc()).all()


def get_faq(db: Session, *, faq_id: int):
    from models import FAQ

    return db.query(FAQ).filter(FAQ.id == faq_id).first()


def create_faq(db: Session, *, question: str, answer: str):
    from models import FAQ

    faq = FAQ(question=question, answer=answer)
    db.add(faq)
    return faq


def delete_faq(db: Session, *, faq):
    db.delete(faq)
