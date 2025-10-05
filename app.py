from flask import Flask, Response
import requests
import re
import os
import itertools
import time 
import json 

# Initialize the Flask web application
app = Flask(__name__)

# Regex definitions 
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)
TVG_URL_REGEX = re.compile(r'url-tvg="([^"]+)"', re.IGNORECASE)

# ======== Categories (Final Exhaustive Keyword List for Stability) ========
CATEGORIES = {
    # USA CATEGORIES 
    # USA NEWS: Broadened to include all US news/weather for stability (since local-only filtering was unstable).
    "USA News": ["news", "weather", "noticias", "cnn", "fox news", "msnbc", "nbc news", "abc news", "cbs news", "chicago", "illinois", "chgo"], 
    "USA Movies": ["hbo", "cinemax", "starz", "amc", "showtime", "tcm", "movie", "films", "cine", "mgm", "indieplex", "lmn", "lifetime movies"],
    "USA Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids", "disney jr", "cartoonito"],
    # US LATINO: Expanded for maximum capture and stability
    "US LATINO": [
        "telemundo", "univision", "uni mas", "unimas", "galavision", "hispana", "latino", "spanish",
        "estrella tv", "america teve", "cnn en español", "fox deportes", "mega tv", "mtv tres", 
        "universo", "wapa america", "uni", "unvsn", "tele m", "telem", "tudn", "goltv", "tyc sports", 
        "cinelatino", "cine estrella", "teleritmo", "bandamax", "de pelicula", "pasiones tv", "mas chic", 
        "vme", "hitn", "tele n"
    ],
    
    # MEXICO CATEGORIES 
    "Mexico News": ["televisa", "tv azteca", "milenio", "imagen", "foro tv", "forotv", "noticias", "news"],
    "Mexico General": [
        "las estrellas", "azteca uno", "canal 2", "televisa", "azteca", "canal 4", "general", # General
        "cine", "canal 5", "canal once", "cinema", "peliculas", # Movies
        "mexico", "telemundo", "univision", "uni mas", "unimas", # Latino
        "sports", "futbol", "fútbol", "deportes" # Sports 
    ],
    "Mexico Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids", "infantil", "ninos", "niños", "discovery kids", "cartoonito", "junior", "kids"],
    
    # GLOBAL/SPORTS CATEGORIES (US-priority sports only)
    "Sports": ["sports", "football", "baseball", "basketball", "soccer", "tennis", "golf", "fighting", "nba", "nfl", "mlb", "espn"],
    
    # GENERAL/MISC: Catches all remaining US networks
    "USA General": ["general", "entertainment", "local", "abc", "cbs", "fox", "nbc", "pbs", "a&e", "bravo", "cmt", "comedy central", "e! entertainment", 
                    "freeform", "fx", "fxx", "fyi", "hln", "ion", "ion plus", "lifetime", "logo", "mav tv", "me tv", 
                    "mtv", "vice", "bet", "gsn", "txa21", "wciu", "the u", "cozi", "grit", "get tv", "buzzr", 
                    "documentary", "history", "science", "travel", "animal planet", "id", 
                    "investigation", "crime", "hgtv", "cooking channel", "food network", "weather"],
}

# --- Category Sets for Logic Flow ---
US_CATEGORY_NAMES = {"USA News", "USA Movies", "USA Kids", "US LATINO", "Documentary", "Sports", "USA General"}
MEXICO_CATEGORY_NAMES = {"Mexico News", "Mexico General", "Mexico Kids"}
# --- End Category Sets ---

# Acceptable prefixes for initial filtering
ACCEPTABLE_PREFIXES = ('US|', 'MX|', 'MXC|')


# ======== Helper Functions (No LLM) ========

def add_group_title(extinf_line, category, display_name):
    """Adds or replaces the 'group-title' attribute and sets the final display name."""
    if 'group-title' in extinf_line.lower():
        modified_line = re.sub(r'group-title=".*?"', f'group-title="{category}"', extinf_line, count=1, flags=re.IGNORECASE)
    else:
        m = re.match(r'(#EXTINF:[^\n\r]*?)(,)(.*)', extinf_line)
        if m:
            attributes = m.group(1)
            modified_line = f'{attributes} group-title="{category}"{m.group(2)}{m.group(3)}'
        else:
            modified_line = extinf_line
    
    # Update/Set the clean display name
    final_line = re.sub(r',.*$', f',{display_name}', modified_line)
    return final_line

def stream_and_categorize(lines_iterator, tvg_url=None):
    """
    Generator that processes the M3U line-by-line using stable keyword logic.
    """
    seen_streams = set()
    
    header = '#EXTM3U'
    if tvg_url:
        header += f' url-tvg="{tvg_url}"'
    yield header + '\n'

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
            
            display_match = EXTINF_REGEX.match(current_ext)
            if not display_match:
                current_ext = None
                continue

            display_name = display_match.group(2).strip()
            display_lower = display_name.lower()
            display_upper = display_name.upper()
            
            # --- 1. Prefix Filter & Region Determination ---
            if display_upper.startswith('US|'):
                target_categories = US_CATEGORY_NAMES
                fallback_category = "USA General"
            elif display_upper.startswith(('MX|', 'MXC|')):
                target_categories = MEXICO_CATEGORY_NAMES
                fallback_category = "Mexico General"
            else:
                current_ext = None 
                continue

            # --- 2. De-Duplication Check ---
            if line in seen_streams:
                current_ext = None 
                continue
            seen_streams.add(line)

            # --- 3. Keyword Categorization (Priority Check) ---
            found = None
            for cat_name in target_categories:
                keywords = CATEGORIES.get(cat_name)
                if keywords and any(kw in display_lower for kw in keywords):
                    found = cat_name
                    break
            
            # --- 4. Final Fallback Logic ---
            final_group = found if found else fallback_category

            # --- 5. Final Formatting and Prefix Removal ---
            
            new_display_name = display_name
            for prefix in ACCEPTABLE_PREFIXES:
                if new_display_name.upper().startswith(prefix):
                    new_display_name = new_display_name[len(prefix):].lstrip()
                    break

            attributes = display_match.group(1).strip()
            modified_ext_line = add_group_title(current_ext, final_group, new_display_name)
            
            yield modified_ext_line + '\n'
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
    """
    Fetches the source M3U using a retry mechanism, extracts the EPG URL,
    and streams the result to avoid memory issues.
    """
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    
    if not username or not password:
        return Response("ERROR: IPTV credentials (USERNAME or PASSWORD) not set.", mimetype="text/plain", status=500)

    # Use the only known stable host (with built-in retry logic)
    host = "http://line.premiumpowers.net"
    
    # --- CRITICAL CHANGE: output=hls for improved streaming stability ---
    m3u_url_template = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=hls"

    successful_response = None
    lines_to_process = None
    tvg_url = None
    last_error = "Initial attempt failed."
    
    # Robust Retry Loop (up to 5 attempts)
    for attempt in range(5):
        m3u_url = m3u_url_template
        print(f"Attempting connection to: {host} (Attempt {attempt + 1}/5)")
        
        try:
            r = requests.get(m3u_url, timeout=300, stream=True) 
            r.raise_for_status() 
            
            raw_lines_iterator = r.iter_lines()
            first_line_raw = next(raw_lines_iterator, b'')
            first_line = first_line_raw.decode('utf-8').strip()
            
            if first_line.startswith('#EXTM3U'):
                successful_response = r
                
                tvg_match = TVG_URL_REGEX.search(first_line)
                if tvg_match:
                    tvg_url = tvg_match.group(1)

                lines_to_process = itertools.chain([first_line_raw], raw_lines_iterator)
                break 
            else:
                last_error = f"Host {host} returned content that didn't start with #EXTM3U."
                print(last_error)

        except requests.exceptions.RequestException as e:
            last_error = f"Host {host} failed with error: {e}"
            print(last_error)
        
        time.sleep(5) 

    if successful_response:
        # Pass the extracted EPG URL to the generator
        return Response(stream_and_categorize(lines_to_process, tvg_url), mimetype="application/x-mpegurl")
    else:
        print("FATAL: All attempts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U after 5 retries. Last error was: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (unchanged) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
