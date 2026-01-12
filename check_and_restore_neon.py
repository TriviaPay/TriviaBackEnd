#!/usr/bin/env python3
"""
Script to check database state and provide Neon backup restoration instructions.
"""
import os
import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import inspect, text

from core.db import engine


def check_database_state():
    """Check current database state"""
    print("=" * 70)
    print("DATABASE STATE CHECK")
    print("=" * 70)
    print()

    try:
        conn = engine.connect()
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        print(f"✅ Connected to database")
        print(f"📊 Total tables: {len(tables)}")
        print()

        # Check critical tables
        critical_tables = {
            "users": "Users",
            "global_chat_messages": "Global Chat Messages",
            "private_chat_conversations": "Private Chat Conversations",
            "private_chat_messages": "Private Chat Messages",
            "trivia_live_chat_messages": "Trivia Live Chat Messages",
            "chat_mute_preferences": "Chat Mute Preferences",
            "onesignal_players": "OneSignal Players",
        }

        print("Critical Tables Status:")
        print("-" * 70)
        for table, name in critical_tables.items():
            exists = table in tables
            status = "✅ EXISTS" if exists else "❌ MISSING"
            print(f"{status:12} {name:35} ({table})")

            if exists:
                try:
                    result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = result.fetchone()[0]
                    print(f"             └─ Rows: {count}")
                except Exception as e:
                    print(f"             └─ Error checking rows: {e}")

        print()
        print("=" * 70)
        print("DATA RECOVERY STATUS")
        print("=" * 70)
        print()

        # Check users
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM users"))
            user_count = result.fetchone()[0]

            if user_count == 0:
                print("⚠️  WARNING: Users table is EMPTY!")
                print("   Your user data appears to be missing.")
                print("   You need to restore from a Neon backup.")
            else:
                print(f"✅ Users found: {user_count}")
                print("   Your user data appears to be intact.")
        except Exception as e:
            print(f"❌ Error checking users: {e}")

        conn.close()

    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


def show_neon_restore_instructions():
    """Show instructions for restoring from Neon backup"""
    print()
    print("=" * 70)
    print("HOW TO RESTORE FROM NEON BACKUP")
    print("=" * 70)
    print()

    db_url = os.getenv("DATABASE_URL", "")

    if "neon" in db_url.lower() or "ep-" in db_url.lower():
        print("✅ Neon Database Detected")
        print()
        print("Neon automatically creates backups. Follow these steps:")
        print()
        print("STEP 1: Access Neon Console")
        print("-" * 70)
        print("1. Go to: https://console.neon.tech")
        print("2. Log in with your Neon account")
        print()
        print("STEP 2: Find Your Project")
        print("-" * 70)
        print("3. Select your project from the dashboard")
        print("4. Click on 'Branches' in the left sidebar")
        print()
        print("STEP 3: Access Time Travel / Backups")
        print("-" * 70)
        print("5. Click on your branch (usually 'main')")
        print("6. Go to 'Time Travel' or 'Backups' tab")
        print("7. You'll see a timeline of automatic backups")
        print("   (Neon keeps backups for 7 days by default)")
        print()
        print("STEP 4: Select Backup Point")
        print("-" * 70)
        print("8. Find a backup from BEFORE tables were deleted")
        print("9. Click on the backup point you want to restore")
        print()
        print("STEP 5: Create Restore Branch")
        print("-" * 70)
        print("10. Click 'Create Branch' or 'Restore' button")
        print("11. This creates a NEW branch with data from that backup")
        print("12. Give it a name like 'restored-backup-2025-11-22'")
        print()
        print("STEP 6: Get New Connection String")
        print("-" * 70)
        print("13. Click on the newly created branch")
        print("14. Go to 'Connection Details' or 'Settings'")
        print("15. Copy the 'Connection String'")
        print("    (It will be different from your current one)")
        print()
        print("STEP 7: Update Your Environment")
        print("-" * 70)
        print("16. Open your .env file")
        print("17. Update DATABASE_URL with the new connection string")
        print("18. Save the file")
        print()
        print("STEP 8: Verify Restoration")
        print("-" * 70)
        print("19. Run this script again to verify data is restored:")
        print("    python3 check_and_restore_neon.py")
        print("20. Restart your server")
        print()
        print("=" * 70)
        print("IMPORTANT NOTES")
        print("=" * 70)
        print()
        print("⚠️  Restoring creates a NEW branch - your current branch stays unchanged")
        print("✅ You can switch between branches by updating DATABASE_URL")
        print("🔄 After restoring, you may want to merge data back to main branch")
        print("📅 Neon keeps automatic backups for 7 days by default")
        print("💾 Consider setting up longer retention if needed")
        print()
        print("=" * 70)
        print("ALTERNATIVE: If you have a SQL backup file")
        print("=" * 70)
        print()
        print("If you have a .sql or .dump backup file, you can restore it with:")
        print()
        print("  # Using psql:")
        print("  psql '<your_connection_string>' < backup.sql")
        print()
        print("  # Or using Python:")
        print('  python3 -c "')
        print("  from db import engine")
        print("  with open('backup.sql', 'r') as f:")
        print("      engine.execute(f.read())")
        print('  "')
        print()
    else:
        print("⚠️  Not a Neon database detected")
        print(
            "Current DATABASE_URL pattern:",
            db_url[:60] + "..." if len(db_url) > 60 else db_url,
        )
        print()
        print("If you're using a different provider, check their backup documentation:")
        print("- AWS RDS: Check automated backups in RDS console")
        print("- Heroku: Use pg:backups commands")
        print("- Other providers: Check their backup/restore documentation")


if __name__ == "__main__":
    print()
    print("🔍 Checking database state...")
    print()

    if check_database_state():
        show_neon_restore_instructions()
    else:
        print(
            "\n❌ Could not check database state. Please verify your DATABASE_URL is correct."
        )
        sys.exit(1)
