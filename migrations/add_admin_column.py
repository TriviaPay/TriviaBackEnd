from sqlalchemy import text
from db import engine

def upgrade():
    # Add is_admin column
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE users 
            ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;
        """))
        conn.commit()

def downgrade():
    # Remove is_admin column
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE users 
            DROP COLUMN is_admin;
        """))
        conn.commit()

if __name__ == "__main__":
    upgrade() 