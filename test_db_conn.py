import mysql.connector
import sys

config = {
    'host': '35.207.243.110',
    'port': 3306,
    'user': 'nayagam',
    'password': 'rcvwoWV5O7#TGO7x#5ot'
}

print(f"Connecting to {config['host']}...")
try:
    conn = mysql.connector.connect(**config)
    print("✅ SUCCESS: Remote connection established!")
    conn.close()
except Exception as e:
    print(f"❌ FAILURE: {e}")
    sys.exit(1)
