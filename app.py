from flask import Flask, Response
import requests
import re
import os
import itertools
import json # <-- NEW IMPORT for handling API data

# Initialize the Flask web application
app = Flask(__name__)

# --- LLM API Setup ---
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # NEW REQUIRED ENVIRONMENT VARIABLE

# Regex to capture attributes (Group 1) and display name (Group 2)
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)

# ======== Categories (Your pre-defined groups remain for fast access) ========
CATEGORIES = {
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

# ======== Helper Functions ========

def add_group_title(extinf_line, category):
    """Adds or replaces the 'group-title' attribute."""
    if 'group-title' in extinf_line.lower():
        return re.sub(r'group-title=".*?"', f'group-title="{category}"', extinf_line, count=1, flags=re.IGNORECASE)
    
    match = EXTINF_REGEX.match(extinf_line)
    if match:
        attributes = match.group(1).strip()
        display_name = match.group(2).strip()
        return f'{attributes} group-title="{category}",{display_name}'

    return extinf_line

def get_llm_category(channel_name):
    """Uses Gemini with Google Search to categorize a channel name, returning a string category."""
    if not GEMINI_API_KEY:
        return None

    # System instruction guides the LLM to output a single, usable category
    system_prompt = "You are an IPTV channel categorization engine. Analyze the channel name. Use Google Search to find its primary region, language, and genre. Output ONLY a single, descriptive category name (e.g., 'French News', 'USA Kids', 'Global Sports'). If categorization is impossible, output 'Uncategorized'."
    
    user_query = f"Categorize the channel: {channel_name}"
    
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "tools": [{"google_search": {}}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
    }
    
    headers = {'Content-Type': 'application/json'}
    
    try:
        # Set a reasonable timeout for the API call (20s is plenty for text generation)
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
    """
    Processes the M3U line-by-line, prioritizing keywords, then using the LLM for smart categorization, 
    and finally filtering based on your desired regions.
    """
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
                # The LLM categorization runs only if the fast keyword check failed
                llm_category = get_llm_category(display)
                if llm_category:
                    found = llm_category # Use the LLM's generated category

            # === FINAL FILTERING LOGIC ===
            
            # If no category was found (keyword or LLM), skip this channel.
            if not found:
                current_ext = None
                continue
            
            # If the category found is NOT one of our predefined US/Mexico groups, 
            # we check if the LLM output (stored in 'found') contains US/Mexico regions.
            if found not in CATEGORIES:
                # We are checking the LLM-generated string for US or Mexico keywords.
                llm_cat_lower = found.lower()
                
                # If the LLM output is NOT relevant to the user's desired regions, filter it out.
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
    """Fetches the source M3U using a fallback list of hosts and streams the result."""
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    
    if not username or not password:
        return Response("ERROR: Authentication credentials (USERNAME or PASSWORD) are not set.", mimetype="text/plain", status=500)

    # Hardcoded list of IPTV provider hosts for automatic failover
    hosts = [
        "http://line.premiumpowers.net",
        "http://servidorgps.org",
        "http://EdgesBuddySad.h1ott.com",
        "http://superberiln24.com"
    ]

    successful_response = None
    last_error = "No host attempted yet."
    
    for host in hosts:
        m3u_url = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
        print(f"Attempting connection to: {host}")
        
        try:
            r = requests.get(m3u_url, timeout=300, stream=True) 
            r.raise_for_status()
            
            lines_iterator = r.iter_lines()
            first_line = next(lines_iterator, b'').decode('utf-8').strip()
            
            if first_line.startswith('#EXTM3U'):
                successful_response = r
                break
            else:
                last_error = f"Host {host} returned content that didn't start with #EXTM3U."
                print(last_error)

        except requests.exceptions.RequestException as e:
            last_error = f"Host {host} failed with error: {e}"
            print(last_error)

    if successful_response:
        lines_to_process = itertools.chain([first_line.encode('utf-8')], successful_response.iter_lines())
        
        # Flask Response streams the output using the generator
        return Response(stream_and_categorize(lines_to_process), mimetype="application/x-mpegurl")
    else:
        print("FATAL: All hosts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U from any host. Last error: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (for local testing, Render uses Gunicorn) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
