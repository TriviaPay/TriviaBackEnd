import sys
import os
import logging
from sqlalchemy import text, inspect
from datetime import datetime

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now import from the main app
from db import get_db, engine
from models import CountryCode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# List of common country codes with their data
COUNTRY_CODES = [
    {
        "code": "+1",
        "country_name": "United States",
        "flag_url": "https://flagsapi.com/US/animated/64.gif",
        "country_iso": "US"
    },
    {
        "code": "+1",
        "country_name": "Canada",
        "flag_url": "https://flagsapi.com/CA/animated/64.gif",
        "country_iso": "CA"
    },
    {
        "code": "+44",
        "country_name": "United Kingdom",
        "flag_url": "https://flagsapi.com/GB/animated/64.gif",
        "country_iso": "GB"
    },
    {
        "code": "+91",
        "country_name": "India",
        "flag_url": "https://flagsapi.com/IN/animated/64.gif",
        "country_iso": "IN"
    },
    {
        "code": "+61",
        "country_name": "Australia",
        "flag_url": "https://flagsapi.com/AU/animated/64.gif",
        "country_iso": "AU"
    },
    {
        "code": "+49",
        "country_name": "Germany",
        "flag_url": "https://flagsapi.com/DE/animated/64.gif",
        "country_iso": "DE"
    },
    {
        "code": "+33",
        "country_name": "France",
        "flag_url": "https://flagsapi.com/FR/animated/64.gif",
        "country_iso": "FR"
    },
    {
        "code": "+81",
        "country_name": "Japan",
        "flag_url": "https://flagsapi.com/JP/animated/64.gif",
        "country_iso": "JP"
    },
    {
        "code": "+55",
        "country_name": "Brazil",
        "flag_url": "https://flagsapi.com/BR/animated/64.gif",
        "country_iso": "BR"
    },
    {
        "code": "+86",
        "country_name": "China",
        "flag_url": "https://flagsapi.com/CN/animated/64.gif",
        "country_iso": "CN"
    },
    {
        "code": "+7",
        "country_name": "Russia",
        "flag_url": "https://flagsapi.com/RU/animated/64.gif",
        "country_iso": "RU"
    },
    {
        "code": "+27",
        "country_name": "South Africa",
        "flag_url": "https://flagsapi.com/ZA/animated/64.gif",
        "country_iso": "ZA"
    },
    {
        "code": "+52",
        "country_name": "Mexico",
        "flag_url": "https://flagsapi.com/MX/animated/64.gif",
        "country_iso": "MX"
    },
    {
        "code": "+971",
        "country_name": "United Arab Emirates",
        "flag_url": "https://flagsapi.com/AE/animated/64.gif",
        "country_iso": "AE"
    },
    {
        "code": "+34",
        "country_name": "Spain",
        "flag_url": "https://flagsapi.com/ES/animated/64.gif",
        "country_iso": "ES"
    },
    {
        "code": "+39",
        "country_name": "Italy",
        "flag_url": "https://flagsapi.com/IT/animated/64.gif",
        "country_iso": "IT"
    },
    {
        "code": "+65",
        "country_name": "Singapore",
        "flag_url": "https://flagsapi.com/SG/animated/64.gif",
        "country_iso": "SG"
    },
    {
        "code": "+92",
        "country_name": "Pakistan",
        "flag_url": "https://flagsapi.com/PK/animated/64.gif",
        "country_iso": "PK"
    }
]

def populate_country_codes():
    """
    Populate the country_codes table with common country codes and their flag images.
    """
    try:
        # Create a DB session
        db = next(get_db())
        
        # Check if table exists - create it if not
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        if "country_codes" not in tables:
            logger.info("Creating country_codes table")
            CountryCode.__table__.create(engine, checkfirst=True)
            logger.info("Created country_codes table")
        else:
            logger.info("country_codes table exists")
        
        # Try to access the table to see if it exists
        try:
            # Check how many entries we already have
            existing_count = db.query(CountryCode).count()
            logger.info(f"Found {existing_count} existing country code entries")
            
            # If we have entries, don't populate again
            if existing_count > 0:
                logger.info("Country codes table already populated, skipping")
                db.close()
                return True
        except Exception as e:
            logger.error(f"Error querying country_codes table: {str(e)}")
            logger.info("Will create the table and populate it")
            CountryCode.__table__.create(engine, checkfirst=True)
        
        # Add each country code
        added_count = 0
        for country_data in COUNTRY_CODES:
            try:
                # Create new country code entry
                country_code = CountryCode(
                    code=country_data["code"],
                    country_name=country_data["country_name"],
                    flag_url=country_data["flag_url"],
                    country_iso=country_data["country_iso"],
                    created_at=datetime.utcnow()
                )
                
                db.add(country_code)
                added_count += 1
                logger.info(f"Added country code {country_data['code']} for {country_data['country_name']}")
            except Exception as e:
                db.rollback()
                logger.error(f"Error adding country code {country_data['code']}: {str(e)}")
                continue
        
        # Commit changes to database
        db.commit()
        logger.info(f"Successfully added {added_count} country codes")
        db.close()
        return True
    except Exception as e:
        logger.error(f"Error populating country codes: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting migration to populate country codes")
    print("Starting migration to populate country codes")
    success = populate_country_codes()
    if success:
        logger.info("Migration completed successfully")
        print("Migration completed successfully")
    else:
        logger.error("Migration failed")
        print("Migration failed") 