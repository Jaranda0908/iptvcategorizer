from flask import Flask, Response
import requests
import re
import os
import itertools
import time # Used for retry logic delay
import json # Used for API call payload

# Initialize the Flask web application
app = Flask(__name__)

# Regex definitions (unchanged)
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)
TVG_URL_REGEX = re.compile(r'url-tvg="([^"]+)"', re.IGNORECASE)

# --- LLM API Configuration ---
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"
# Base URL for the API call (will be built with the key later)
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"

# ======== Categories (Final, Bulletproof List) ========
CATEGORIES = {
    # USA CATEGORIES (Strictly enforced for US| streams only)
    "USA News": ["chicago", "illinois", "chgo"], 
    "USA Movies": ["hbo", "cinemax", "starz", "amc", "showtime", "tcm", "movie", "christmas", "films"],
    "USA Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids", "disney jr", "cartoonito"],
    "US LATINO": [
        "telemundo", "univision", "uni mas", "unimas", "galavision", "hispana", "latino", "spanish",
        "estrella tv", "america teve", "cnn en español", "cine mexicano", "discovery en español",
        "discovery familia", "espn deportes", "fox deportes", "mega tv", "mtv tres", "universo", "vme",
        "wapa america", "uni", "unvsn", "tele m", "telem"
    ],
    "Documentary": ["nat geo", "discovery", "history", "documentary", "science", "travel"],
    "Adult": ["xxx", "porn", "adult", "eros"],
    
    # MEXICO CATEGORIES (Strictly enforced for MX| / MXC| streams only)
    "Mexico News": ["televisa", "tv azteca", "milenio", "imagen", "foro tv", "forotv", "noticias", "news"],
    "Mexico Movies": ["cine", "canal 5", "canal once", "cinema", "peliculas"],
    "Mexico Kids": [
        "cartoon", "nick", "disney", "boomerang", "pbskids", "infantil", "ninos", "niños", "discovery kids", 
        "cartoonito", "junior", "kids"
    ],
    "Mexico General": ["las estrellas", "azteca uno", "canal 2", "televisa", "azteca", "canal 4", "general"],
    
    # GLOBAL/SPORTS CATEGORIES (Function as US-priority sports)
    "Basketball": ["nba", "basketball"],
    "Football": ["nfl", "football", "college football", "espn college"],
    "Baseball": ["mlb", "baseball"],
    "Soccer": ["soccer", "champions", "premier league", "laliga"],
    "Tennis": ["tennis", "atp", "wta"],
    "Golf": ["golf", "pga"],
    "Fighting": ["ufc", "boxing", "mma", "wwe", "fight"],
    "eSports": ["esports", "gaming", "twitch"],
}

# Acceptable prefixes for initial filtering
ACCEPTABLE_PREFIXES = ('US|', 'MX|', 'MXC|')

# Define which categories belong to which region for prioritization
US_CATEGORY_NAMES = {"USA News", "USA Movies", "USA Kids", "US LATINO", "Documentary", "Adult"}
MEXICO_CATEGORY_NAMES = {"Mexico News", "Mexico Movies", "Mexico Kids", "Mexico General"}
GLOBAL_CATEGORY_NAMES = CATEGORIES.keys() - US_CATEGORY_NAMES - MEXICO_CATEGORY_NAMES

# List of all category names for LLM prompt
ALL_CATEGORY_NAMES = list(CATEGORIES.keys()) + ["USA General", "Mexico General"]

# ======== New LLM Helper Function (Safe and Targeted) ========

def get_llm_category(channel_name, api_key, is_mexican):
    """
    Uses Gemini API to categorize a channel that failed the keyword check.
    Returns the new category name or None.
    """
    if not api_key:
        return None
    
    # Give the model all possible valid categories to choose from
    category_list = ALL_CATEGORY_NAMES
    
    # System instruction focuses the model on the task and response format
    system_prompt = (
        "You are an M3U playlist categorizer. Analyze the channel name and select the BEST matching group title "
        "from the following list: " + ", ".join(category_list) + ". "
        "Respond with ONLY the selected group title string, NOTHING ELSE."
    )
    
    # Prompt asks the model to categorize and check the origin
    user_query = (
        f"Categorize this channel name: '{channel_name}'. "
        f"If you can confirm its origin is not US, Mexican, or Latino, select 'USA General' or 'Mexico General' based on the language/region, "
        "but only use US/Mexico/Latino specific categories if the channel is confirmed to be local or relevant."
    )

    url = f"{GEMINI_API_BASE_URL}{GEMINI_MODEL}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "tools": [{"google_search": {}}], # Use Google Search for grounding
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "config": {"temperature": 0.1} # Lower temperature for stable categorization
    }

    try:
        # Use a short timeout for the LLM call; if it takes too long, we fall back to General
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        response.raise_for_status()
        
        result = response.json()
        
        # Safely extract the generated text response
        text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
        
        # Validate that the response is one of the allowed categories
        if text in category_list:
            return text
        else:
            print(f"LLM returned invalid category: {text}")
            return None # Invalid response, treat as unclassified
            
    except requests.exceptions.RequestException as e:
        print(f"Gemini API call failed: {e}")
        return None

# ======== Core Processing Function (Updated for LLM) ========

def stream_and_categorize(lines_iterator, tvg_url=None, api_key=None):
    """
    Generator that processes the M3U line-by-line, including LLM fallback.
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
            display_upper = display_name.upper()
            display_lower = display_name.lower()
            
            # --- 1. Crucial Prefix Filter (unchanged) ---
            if not display_upper.startswith(ACCEPTABLE_PREFIXES):
                current_ext = None 
                continue

            if line in seen_streams:
                current_ext = None 
                continue
            
            seen_streams.add(line)

            # --- 2. Determine Target Categories based on Prefix ---
            if display_upper.startswith('US|'):
                target_categories = US_CATEGORY_NAMES.union(GLOBAL_CATEGORY_NAMES)
                is_mexican_stream = False
            elif display_upper.startswith(('MX|', 'MXC|')):
                target_categories = MEXICO_CATEGORY_NAMES 
                is_mexican_stream = True
            else:
                current_ext = None 
                continue

            # --- 3. Categorization Check (Keyword First) ---
            found = None
            for cat_name in target_categories:
                keywords = CATEGORIES.get(cat_name)
                if keywords and any(kw in display_lower for kw in keywords):
                    found = cat_name
                    break
            
            # --- 4. LLM Fallback (Targeted Smart Categorization) ---
            if not found and api_key:
                print(f"No keyword match for {display_name}. Calling LLM...")
                llm_category = get_llm_category(display_name, api_key, is_mexican_stream)
                
                # If LLM returns a valid category, use it
                if llm_category:
                    found = llm_category
            
            # --- 5. Final Fallback Logic (General Groups) ---
            if not found:
                found = "Mexico General" if is_mexican_stream else "USA General"
            
            # --- 6. Final Formatting and Prefix Removal ---
            new_display_name = display_name
            for prefix in ACCEPTABLE_PREFIXES:
                if new_display_name.upper().startswith(prefix):
                    new_display_name = new_display_name[len(prefix):].lstrip()
                    break

            attributes = display_match.group(1).strip()
            modified_ext_line = f'{attributes} group-title="{found}",{new_display_name}'
            
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
    and streams the result to avoid memory issues and uses LLM for final categorization.
    """
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not username or not password:
        return Response("ERROR: IPTV credentials (USERNAME or PASSWORD) not set.", mimetype="text/plain", status=500)

    # Use the only known stable host (with built-in retry logic)
    host = "http://line.premiumpowers.net"
    m3u_url_template = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

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
        # Pass the extracted EPG URL and API Key to the generator
        return Response(stream_and_categorize(lines_to_process, tvg_url, api_key), mimetype="application/x-mpegurl")
    else:
        print("FATAL: All attempts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U after 5 retries. Last error was: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (unchanged) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
