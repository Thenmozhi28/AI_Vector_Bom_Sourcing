from database import get_connection
import json

def main():
    conn, db_type = get_connection()
    cursor = conn.cursor(dictionary=True) if db_type == "mysql" else conn.cursor()
    
    # 1. Query the matched Friwo product
    cursor.execute("SHOW TABLES")
    print("TABLES IN DATABASE:")
    for t in cursor.fetchall():
        print(t)
    conn.close()

if __name__ == "__main__":
    main()
