from flask import Flask, Response
import requests

app = Flask(__name__)

# ===== Replace this with your actual M3U URL =====
M3U_URL = "http://line.premiumpowers.net/get.php?username=1d0c233137&password=5bcc23b7e8&type=m3u_plus&output=ts"

# Cache variable
M3U_CACHE = None

def fetch_m3u():
    global M3U_CACHE
    try:
        print("Fetching M3U playlist...")
        r = requests.get(M3U_URL, timeout=15)  # 15 seconds timeout
        r.raise_for_status()
        M3U_CACHE = r.text
        print("M3U fetched successfully!")
    except Exception as e:
        print("Error fetching M3U:", e)
        M3U_CACHE = None

# Fetch once at startup
fetch_m3u()

@app.route("/")
def home():
    return "Flask app is running! Visit /m3u for your playlist."

@app.route("/m3u")
def get_m3u():
    if M3U_CACHE:
        return Response(M3U_CACHE, mimetype="audio/x-mpegurl")
    else:
        return "M3U not available", 503

if __name__ == "__main__":
    # Local development only
    app.run(host="0.0.0.0", port=5000, debug=True)
