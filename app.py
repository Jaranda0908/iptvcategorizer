from flask import Flask, Response
import requests
import threading
import time

app = Flask(__name__)

# ------------------ CONFIG ------------------
M3U_URL = "http://line.premiumpowers.net/get.php?username=1d0c233137&password=5bcc23b7e8&type=m3u_plus&output=ts"
FETCH_TIMEOUT = 30          # seconds for requests
REFRESH_INTERVAL = 2 * 60 * 60  # 2 hours in seconds

# ------------------ CACHE ------------------
M3U_CACHE = None

# ------------------ FUNCTIONS ------------------
def fetch_m3u():
    """Fetch the M3U playlist and update cache."""
    global M3U_CACHE
    try:
        print("Fetching M3U playlist...")
        r = requests.get(M3U_URL, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        M3U_CACHE = r.text
        print("M3U fetched successfully!")
    except Exception as e:
        print("Error fetching M3U:", e)

def background_refresh():
    """Background thread that refreshes M3U every REFRESH_INTERVAL seconds."""
    while True:
        fetch_m3u()
        time.sleep(REFRESH_INTERVAL)

# ------------------ ROUTES ------------------
@app.route("/")
def home():
    return "Flask app is running! Visit /m3u for your playlist."

@app.route("/m3u")
def get_m3u():
    global M3U_CACHE
    if not M3U_CACHE:
        # Try fetching immediately if cache is empty
        fetch_m3u()
        if not M3U_CACHE:
            return "M3U not available", 503
    return Response(M3U_CACHE, mimetype="audio/x-mpegurl")

# ------------------ MAIN ------------------
if __name__ == "__main__":
    # Start background thread
    thread = threading.Thread(target=background_refresh, daemon=True)
    thread.start()
    
    # Start Flask app
    app.run(host="0.0.0.0", port=5000)
