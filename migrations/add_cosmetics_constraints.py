import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_constraints():
    with engine.connect() as connection:
        # 1) Unique constraints (retry-safe)
        for stmt, name in [
            ("ALTER TABLE user_avatars ADD CONSTRAINT uq_user_avatars UNIQUE (user_id, avatar_id)", "uq_user_avatars"),
            ("ALTER TABLE user_frames  ADD CONSTRAINT uq_user_frames  UNIQUE (user_id, frame_id)", "uq_user_frames"),
        ]:
            trans = connection.begin()
            try:
                logger.info(f"Adding {name} ...")
                connection.execute(text(stmt))
                trans.commit()
            except Exception as e:
                logger.info(f"Skipping {name} (may already exist): {e}")
                trans.rollback()

        # 2) Clean invalid selected_* references before adding FKs
        trans = connection.begin()
        try:
            logger.info("Nulling invalid users.selected_avatar_id references...")
            connection.execute(text("""
                UPDATE users u
                SET selected_avatar_id = NULL
                WHERE selected_avatar_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM avatars a WHERE a.id = u.selected_avatar_id)
            """))
            logger.info("Nulling invalid users.selected_frame_id references...")
            connection.execute(text("""
                UPDATE users u
                SET selected_frame_id = NULL
                WHERE selected_frame_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM frames f WHERE f.id = u.selected_frame_id)
            """))
            trans.commit()
        except Exception as e:
            logger.error(f"Error cleaning invalid references: {e}")
            trans.rollback()
            raise

        # 3) Add FKs separately (retry-safe)
        for stmt, name in [
            ("""
            ALTER TABLE users
            ADD CONSTRAINT fk_users_selected_avatar
            FOREIGN KEY (selected_avatar_id)
            REFERENCES avatars(id)
            ON DELETE SET NULL
            """, "fk_users_selected_avatar"),
            ("""
            ALTER TABLE users
            ADD CONSTRAINT fk_users_selected_frame
            FOREIGN KEY (selected_frame_id)
            REFERENCES frames(id)
            ON DELETE SET NULL
            """, "fk_users_selected_frame"),
        ]:
            trans = connection.begin()
            try:
                logger.info(f"Adding {name} ...")
                connection.execute(text(stmt))
                trans.commit()
            except Exception as e:
                logger.info(f"Skipping {name} (may already exist): {e}")
                trans.rollback()

if __name__ == "__main__":
    add_constraints()


