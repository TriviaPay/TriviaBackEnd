print("Testing imports...")

try:
    import os
    print("Imported os")
except Exception as e:
    print(f"Error importing os: {str(e)}")

try:
    from db import get_db
    print("Imported get_db from db")
except Exception as e:
    print(f"Error importing get_db: {str(e)}")

try:
    from models import TriviaDrawConfig, TriviaDrawWinner, CompanyRevenue, Transaction
    print("Imported models successfully")
except Exception as e:
    print(f"Error importing models: {str(e)}")

try:
    from routers.dependencies import get_current_user, get_admin_user
    print("Imported dependencies successfully")
except Exception as e:
    print(f"Error importing dependencies: {str(e)}")

try:
    from models import DrawConfig
    print("Imported DrawConfig successfully")
except Exception as e:
    print(f"Error importing DrawConfig: {str(e)}")

try:
    from routers import admin
    print("Imported admin router successfully")
except Exception as e:
    print(f"Error importing admin router: {str(e)}")

try:
    from scheduler import start_scheduler
    print("Imported start_scheduler successfully")
except Exception as e:
    print(f"Error importing start_scheduler: {str(e)}")

print("Done testing imports") 