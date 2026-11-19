"""
RS Components — Database Resilience Layer
========================================
Reads configuration from db_config.json with automatic SQLite fallback.
"""
import os
import json
import time
import logging
import mysql.connector
import sqlite3
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Database")

CONFIG_PATH = Path(__file__).parent / "db_config.json"

def load_db_config():
    """Loads database configuration from JSON file."""
    default_config = {
        "DB_TYPE": "sqlite",
        "DB_HOST": "localhost",
        "DB_PORT": 3306,
        "DB_USER": "root",
        "DB_PASSWORD": "",
        "DB_NAME": "sourcebuddy_db",
        "FALLBACK_TO_SQLITE": True
    }
    
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
                # Merge with defaults
                return {**default_config, **config}
        except Exception as e:
            logger.error(f"❌ Failed to load db_config.json: {e}")
    
    return default_config

def get_connection():
    """Connects to DB based on config.json, with fallback."""
    config = load_db_config()
    
    # If config explicitly says sqlite
    if config.get("DB_TYPE") == "sqlite":
        return sqlite3.connect("sourcebuddy.db", check_same_thread=False), "sqlite"

    # Try MySQL
    try:
        logger.info(f"🔗 Connecting to MySQL ({config['DB_HOST']})...")
        conn = mysql.connector.connect(
            host=config['DB_HOST'],
            port=config['DB_PORT'],
            user=config['DB_USER'],
            password=config['DB_PASSWORD'],
            database=config['DB_NAME'],
            connect_timeout=3
        )
        logger.info("✅ MySQL Connection Successful!")
        return conn, "mysql"
    except Exception as e:
        logger.warning(f"⚠️ MySQL Unavailable: {e}")
        
        if config.get("FALLBACK_TO_SQLITE", True):
            logger.info("🛡️ FALLBACK: Using Local SQLite (sourcebuddy.db)")
            conn = sqlite3.connect("sourcebuddy.db", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn, "sqlite"
            
    return None, None

def execute_query(query, params=None, fetch=False):
    """Database-agnostic query executor."""
    conn, db_type = get_connection()
    if not conn: return None
    
    try:
        if db_type == "sqlite":
            query = query.replace("%s", "?").replace("NOW()", "CURRENT_TIMESTAMP")
        
        cursor = conn.cursor(dictionary=True) if db_type == "mysql" else conn.cursor()
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
            
        if fetch:
            rows = cursor.fetchall()
            return [dict(row) for row in rows] if db_type == "sqlite" else rows
        
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error(f"❌ Query Error: {e}")
        return None
    finally:
        conn.close()

def init_database():
    """Initializes tables and migrates schema with role & licensing fields."""
    logger.info("🚀 Initializing Database Tables...")
    
    # 1. Create standard tables if they don't exist
    execute_query("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY, 
            username VARCHAR(255) UNIQUE, 
            email VARCHAR(255) UNIQUE,
            password VARCHAR(255),
            full_name VARCHAR(255) DEFAULT '',
            is_approved BOOLEAN DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP NULL,
            last_login TIMESTAMP NULL
        )
    """)
    
    execute_query("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            token VARCHAR(255) UNIQUE,
            is_active BOOLEAN DEFAULT 1,
            expires_at TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address VARCHAR(100) DEFAULT '',
            user_agent VARCHAR(255) DEFAULT ''
        )
    """)
    
    execute_query("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            action VARCHAR(255),
            resource VARCHAR(255) DEFAULT '',
            status VARCHAR(100) DEFAULT 'success',
            details TEXT,
            ip_address VARCHAR(100) DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Schema Migration: Add role and valid_until if they don't exist
    try:
        execute_query("ALTER TABLE users ADD COLUMN role VARCHAR(50) DEFAULT 'viewer'")
        logger.info("✨ Migrated database schema: Added 'role' column.")
    except Exception:
        pass

    try:
        execute_query("ALTER TABLE users ADD COLUMN valid_until TIMESTAMP DEFAULT '2036-01-01 00:00:00'")
        logger.info("✨ Migrated database schema: Added 'valid_until' column.")
    except Exception:
        pass
        
    # 3. Seed default users by individual existence check
    try:
        # Cleanup legacy 'nayagam_admin' user if present
        execute_query("DELETE FROM users WHERE username = 'nayagam_admin'")
        
        # Check and Seed Admin (sourcebuddy_admin)
        admin_check = execute_query("SELECT COUNT(*) as count FROM users WHERE username = 'sourcebuddy_admin'", fetch=True)
        if admin_check and admin_check[0]["count"] == 0:
            logger.info("🌱 Seeding 'sourcebuddy_admin'...")
            execute_query(
                """INSERT INTO users (username, email, password, full_name, is_approved, is_admin, role, valid_until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                ("sourcebuddy_admin", "sourcebuddy_admin@sourcebuddy.com", "sb_admin_password", "SourceBuddy (System Admin)", True, True, "admin", "2036-01-01 00:00:00")
            )

        # Check and Seed RS Expert User
        expert_check = execute_query("SELECT COUNT(*) as count FROM users WHERE username = 'rs_expert'", fetch=True)
        if expert_check and expert_check[0]["count"] == 0:
            logger.info("🌱 Seeding 'rs_expert'...")
            execute_query(
                """INSERT INTO users (username, email, password, full_name, is_approved, is_admin, role, valid_until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                ("rs_expert", "rs_expert@rs-components.com", "rs_sourcing_2026", "RS Components Sourcing Expert", True, False, "procurement_expert", "2026-12-31 23:59:59")
            )
            
        # Check and Seed RS Client Admin User
        client_admin_check = execute_query("SELECT COUNT(*) as count FROM users WHERE username = 'rs_admin'", fetch=True)
        if client_admin_check and client_admin_check[0]["count"] == 0:
            logger.info("🌱 Seeding 'rs_admin' (Client Admin)...")
            execute_query(
                """INSERT INTO users (username, email, password, full_name, is_approved, is_admin, role, valid_until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                ("rs_admin", "rs_admin@rs-components.com", "rs_admin_password", "RS Components Admin", True, True, "client_admin", "2036-01-01 00:00:00")
            )
            
        # Update pre-existing 'admin' user role and admin status to ensure backward compatibility
        execute_query("UPDATE users SET role = 'admin', is_admin = 1, is_approved = 1 WHERE username = 'admin'")
        logger.info("✅ Default user credentials verified and seeded.")
    except Exception as e:
        logger.error(f"❌ Failed to seed default users: {e}")

    logger.info("✅ Database Ready.")

if __name__ == "__main__":
    init_database()
