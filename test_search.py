import requests
import time

q = "RAC03-05SK"
url = f"http://localhost:8005/search?query={q.replace(' ', '%20')}&limit=10"

t0 = time.time()
res = requests.get(url)
t1 = time.time()

print(f"Time taken: {t1-t0:.2f}s")
data = res.json()
print(f"Total results: {len(data)}")
for i, r in enumerate(data):
    print(f"Result {i+1}: Score: {r.get('score')} - {r.get('payload', {}).get('mpn')} | {r.get('payload', {}).get('manufacturer')} | {r.get('payload', {}).get('description')}")
