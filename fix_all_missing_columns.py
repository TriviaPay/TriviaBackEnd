#!/usr/bin/env python3
"""
Script to fix all missing columns in database tables to match model definitions.
This ensures all tables have all columns defined in models.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import traceback

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.schema import CreateTable

from db import Base, engine
from models import *


def get_model_columns(model_class):
    """Get all columns from a SQLAlchemy model"""
    columns = {}
    for column in model_class.__table__.columns:
        col_info = {
            "name": column.name,
            "type": str(column.type),
            "nullable": column.nullable,
            "default": column.default,
            "primary_key": column.primary_key,
            "unique": column.unique,
            "index": column.index,
            "foreign_keys": (
                [fk.target_fullname for fk in column.foreign_keys]
                if column.foreign_keys
                else []
            ),
        }
        columns[column.name] = col_info
    return columns


def get_db_columns(table_name, inspector):
    """Get all columns from actual database table"""
    try:
        columns = inspector.get_columns(table_name)
        return {col["name"]: col for col in columns}
    except Exception as e:
        print(f"  ⚠️  Error getting columns for {table_name}: {e}")
        return {}


def generate_alter_table_sql(table_name, column_name, column_info, db_columns):
    """Generate ALTER TABLE SQL to add missing column"""
    col_type = str(column_info["type"])

    # Map SQLAlchemy types to PostgreSQL types
    # Check in order of specificity (more specific first)
    pg_type = None

    # Handle VARCHAR with length
    if "VARCHAR" in col_type and "(" in col_type:
        length = col_type.split("(")[1].split(")")[0]
        pg_type = f"VARCHAR({length})"
    elif "VARCHAR" in col_type or "String" in col_type:
        pg_type = "VARCHAR"
    elif "TEXT" in col_type:
        pg_type = "TEXT"
    elif "BIGINT" in col_type:
        pg_type = "BIGINT"
    elif "INTEGER" in col_type or "Integer" in col_type:
        pg_type = "INTEGER"
    elif "BOOLEAN" in col_type or "Boolean" in col_type:
        pg_type = "BOOLEAN"
    elif "DateTime" in col_type or "TIMESTAMP" in col_type:
        pg_type = "TIMESTAMP WITHOUT TIME ZONE"
    elif "Date" in col_type:
        pg_type = "DATE"
    elif "FLOAT" in col_type or "Float" in col_type:
        pg_type = "FLOAT"
    elif "JSONB" in col_type:
        pg_type = "JSONB"
    elif "UUID" in col_type:
        pg_type = "UUID"
    else:
        # Default to VARCHAR if unknown
        pg_type = "VARCHAR"

    # Build column definition
    nullable = "NULL" if column_info["nullable"] else "NOT NULL"

    sql = f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {pg_type} {nullable}"

    # Add default if specified
    if column_info.get("default"):
        default = column_info["default"]
        if hasattr(default, "arg"):
            default_arg = default.arg
            if isinstance(default_arg, str):
                sql += f" DEFAULT '{default_arg}'"
            elif isinstance(default_arg, (int, float)):
                sql += f" DEFAULT {default_arg}"
            elif isinstance(default_arg, bool):
                sql += f" DEFAULT {str(default_arg).lower()}"
            elif callable(default_arg):
                # For functions like datetime.utcnow, use server default
                if (
                    "datetime" in str(default_arg).lower()
                    or "utcnow" in str(default_arg).lower()
                ):
                    sql = sql.replace("NOT NULL", "NOT NULL DEFAULT CURRENT_TIMESTAMP")
                elif "generate_account_id" in str(default_arg).lower():
                    # Skip default for account_id generation
                    pass

    return sql


def fix_all_tables():
    """Check all tables and add missing columns"""
    print("=" * 70)
    print("FIXING MISSING COLUMNS IN ALL TABLES")
    print("=" * 70)
    print()

    conn = engine.connect()
    inspector = inspect(engine)

    # Get all table names from models
    metadata = Base.metadata
    model_tables = {}

    for table_name, table in metadata.tables.items():
        model_tables[table_name] = table

    print(f"Found {len(model_tables)} model definitions")
    print()

    total_fixed = 0
    total_checked = 0

    for table_name, table in model_tables.items():
        print(f"Checking table: {table_name}")

        # Get model columns
        model_class = None
        for name, obj in globals().items():
            if hasattr(obj, "__tablename__") and obj.__tablename__ == table_name:
                model_class = obj
                break

        if not model_class:
            print(f"  ⚠️  Could not find model class for {table_name}")
            continue

        model_columns = get_model_columns(model_class)
        db_columns = get_db_columns(table_name, inspector)

        missing_columns = []
        for col_name, col_info in model_columns.items():
            total_checked += 1
            if col_name not in db_columns:
                missing_columns.append((col_name, col_info))

        if missing_columns:
            print(f"  ❌ Missing {len(missing_columns)} columns:")
            for col_name, col_info in missing_columns:
                print(f"     - {col_name} ({col_info['type']})")

                try:
                    sql = generate_alter_table_sql(
                        table_name, col_name, col_info, db_columns
                    )
                    print(f"     → Executing: {sql[:80]}...")
                    conn.execute(text(sql))
                    total_fixed += 1
                    print(f"     ✅ Added {col_name}")
                except Exception as e:
                    print(f"     ❌ Error adding {col_name}: {e}")
                    traceback.print_exc()
        else:
            print(f"  ✅ All columns present")

        print()

    conn.commit()
    conn.close()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total columns checked: {total_checked}")
    print(f"Total columns added: {total_fixed}")
    print()

    if total_fixed > 0:
        print("✅ Missing columns have been added!")
        print("   You may need to restart your server.")
    else:
        print("✅ All tables are up to date!")


if __name__ == "__main__":
    try:
        fix_all_tables()
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
