from flask import Flask, Response
import requests
import threading
import time

app = Flask(__name__)

# Your remote M3U URL
M3U_URL = http://line.premiumpowers.net/get.php?username=1d0c233137&password=5bcc23b7e8&type=m3u_plus&output=ts

# Cache settings
cached_m3u = None
cache_timestamp = 0
CACHE_DURATION = 300  # seconds, i.e., refresh every 5 minutes

def update_cache():
    global cached_m3u, cache_timestamp
    while True:
        try:
            r = requests.get(M3U_URL, timeout=30)  # fetch with short timeout
            if r.status_code == 200:
                cached_m3u = r.text
                cache_timestamp = time.time()
                print("M3U cache updated")
            else:
                print(f"Failed to fetch M3U: {r.status_code}")
        except Exception as e:
            print(f"Error fetching M3U: {e}")
        time.sleep(CACHE_DURATION)

# Start background cache thread
threading.Thread(target=update_cache, daemon=True).start()

@app.route("/")
def home():
    return "Flask app is running! Visit /m3u for your playlist."

@app.route("/m3u")
def get_m3u():
    global cached_m3u, cache_timestamp
    # If cache is empty, try to fetch once more
    if not cached_m3u:
        try:
            r = requests.get(M3U_URL, timeout=10)
            if r.status_code == 200:
                cached_m3u = r.text
                cache_timestamp = time.time()
            else:
                return "Failed to fetch M3U", 500
        except Exception as e:
            return f"Error fetching M3U: {e}", 500

    return Response(cached_m3u, mimetype="application/x-mpegURL")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

