from flask import Flask, Response
import requests
import re
import os
import itertools
import json
import time # <-- NEW IMPORT for retries

# Initialize the Flask web application
app = Flask(__name__)

# --- LLM API Setup ---
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 

# Regex to capture attributes (Group 1) and display name (Group 2)
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)

# ======== Categories (Your pre-defined groups remain for fast access) ========
CATEGORIES = {
    # ... (Your categories are unchanged) ...
    "USA News": ["cnn", "fox news", "msnbc", "nbc news", "abc news", "cbs news"],
    "USA Movies": ["hbo", "cinemax", "starz", "amc", "showtime", "tcm", "movie"],
    "USA Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids"],
    "USA General": ["abc", "nbc", "cbs", "fox", "pbs"],
    "Mexico News": ["televisa", "tv azteca", "milenio", "imagen", "foro tv", "forotv"],
    "Mexico Movies": ["cine", "canal 5", "canal once", "cinema"],
    "Mexico Kids": ["canal once niños", "bitme", "kids mexico"],
    "Mexico General": ["las estrellas", "azteca uno", "canal 2", "televisa"],
    "Basketball": ["nba", "basketball"],
    "Football": ["nfl", "football", "college football", "espn college"],
    "Baseball": ["mlb", "baseball"],
    "Soccer": ["soccer", "futbol", "fútbol", "liga mx", "champions", "premier league", "laliga"],
    "Tennis": ["tennis", "atp", "wta"],
    "Golf": ["golf", "pga"],
    "Fighting": ["ufc", "boxing", "mma", "wwe", "fight"],
    "eSports": ["esports", "gaming", "twitch"],
    "Music": ["mtv", "vh1", "music", "radio"],
    "Documentary": ["nat geo", "discovery", "history", "documentary"],
    "Adult": ["xxx", "porn", "adult", "eros"]
}

# ======== Helper Functions (Unchanged) ========

def add_group_title(extinf_line, category):
    # ... (Unchanged logic) ...
    if 'group-title' in extinf_line.lower():
        return re.sub(r'group-title=".*?"', f'group-title="{category}"', extinf_line, count=1, flags=re.IGNORECASE)
    
    match = EXTINF_REGEX.match(extinf_line)
    if match:
        attributes = match.group(1).strip()
        display_name = match.group(2).strip()
        return f'{attributes} group-title="{category}",{display_name}'

    return extinf_line

def get_llm_category(channel_name):
    # ... (Unchanged logic for LLM API call) ...
    if not GEMINI_API_KEY:
        return None

    system_prompt = "You are an IPTV channel categorization engine. Analyze the channel name. Use Google Search to find its primary region, language, and genre. Output ONLY a single, descriptive category name (e.g., 'French News', 'USA Kids', 'Global Sports'). If categorization is impossible, output 'Uncategorized'."
    user_query = f"Categorize the channel: {channel_name}"
    
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "tools": [{"google_search": {}}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
    }
    
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", 
            headers=headers, 
            data=json.dumps(payload),
            timeout=20 
        )
        response.raise_for_status()
        
        result = response.json()
        
        text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', 'Uncategorized').strip()
        
        if text.lower() == 'uncategorized' or not text:
            return None
            
        return text.replace('"', '').strip()
        
    except Exception as e:
        print(f"Gemini categorization failed for '{channel_name}': {e}")
        return None


def stream_and_categorize(lines_iterator):
    # ... (Unchanged logic for streaming, categorization, and filtering) ...
    yield '#EXTM3U\n'

    current_ext = None
    
    for raw_line in lines_iterator:
        try:
            line = raw_line.decode('utf-8').strip()
        except UnicodeDecodeError:
            continue

        if line.startswith('#EXTINF'):
            current_ext = line
            continue
        
        if current_ext and (line.startswith('http') or line.startswith('rtmp')):
            
            match = EXTINF_REGEX.match(current_ext)
            if not match:
                current_ext = None
                continue
                
            display = match.group(2).strip()
            display_lower = display.lower()
            
            found = None
            
            # 1. KEYWORD CHECK (FAST)
            for cat, keywords in CATEGORIES.items():
                if any(kw in display_lower for kw in keywords):
                    found = cat
                    break
            
            # 2. SMART LLM CHECK (SLOW, uses API)
            if not found and GEMINI_API_KEY:
                llm_category = get_llm_category(display)
                if llm_category:
                    found = llm_category

            # === FINAL FILTERING LOGIC ===
            if not found:
                current_ext = None
                continue
            
            if found not in CATEGORIES:
                llm_cat_lower = found.lower()
                
                if not any(region in llm_cat_lower for region in ['usa', 'us', 'mexico', 'latino', 'spanish']):
                    current_ext = None
                    continue

            # If categorized or approved by LLM filter, yield the channel.
            new_ext = add_group_title(current_ext, found)
            yield new_ext + '\n'
            yield line + '\n'

            current_ext = None

        elif current_ext:
            current_ext = None

# ======== Routes (The Web URLs) ========

@app.route("/")
def home():
    """Simple status page."""
    return "The M3U Categorizer is running! Get your updated playlist from /m3u."

@app.route("/m3u")
def get_m3u():
    """Fetches the source M3U using the one known good host and includes a retry mechanism."""
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    
    if not username or not password:
        return Response("ERROR: Authentication credentials (USERNAME or PASSWORD) are not set.", mimetype="text/plain", status=500)

    # 1. Use ONLY the host that showed successful connection in the logs.
    host = "http://line.premiumpowers.net"
    m3u_url = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

    MAX_RETRIES = 3
    RETRY_DELAY = 10 # seconds

    successful_response = None
    last_error = "Connection attempt failed."
    
    for attempt in range(MAX_RETRIES):
        print(f"Attempting connection to: {host} (Attempt {attempt + 1}/{MAX_RETRIES})")
        
        try:
            r = requests.get(m3u_url, timeout=300, stream=True) 
            r.raise_for_status() 
            
            lines_iterator = r.iter_lines()
            first_line = next(lines_iterator, b'').decode('utf-8').strip()
            
            if first_line.startswith('#EXTM3U'):
                successful_response = r
                break # Success! Break out of the retry loop
            else:
                last_error = f"Host {host} returned content that didn't start with #EXTM3U (Status: {r.status_code})."
                print(last_error)

        except requests.exceptions.RequestException as e:
            last_error = f"Host {host} failed with error: {e}"
            print(last_error)

        # Only retry if it's not the last attempt
        if attempt < MAX_RETRIES - 1:
            print(f"Waiting {RETRY_DELAY} seconds before retrying...")
            time.sleep(RETRY_DELAY)


    # If a successful streaming response was found, pass it to the generator
    if successful_response:
        lines_to_process = itertools.chain([first_line.encode('utf-8')], successful_response.iter_lines())
        
        # Flask Response streams the output using the generator
        return Response(stream_and_categorize(lines_to_process), mimetype="application/x-mpegurl")
    else:
        # All retry attempts failed
        print("FATAL: All retry attempts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U after {MAX_RETRIES} attempts. Last error: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (for local testing, Render uses Gunicorn) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
