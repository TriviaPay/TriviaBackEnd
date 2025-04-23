from db import SessionLocal
from models import UserGemPurchase

def check_purchases():
    db = SessionLocal()
    try:
        purchases = db.query(UserGemPurchase).all()
        print(f"Total one-time purchase records: {len(purchases)}")
        
        for p in purchases[:5]:
            print(f"User {p.user_id} purchased package {p.package_id} for ${p.price_paid}")
            
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    check_purchases() 