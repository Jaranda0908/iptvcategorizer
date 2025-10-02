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

# --- LLM API Configuration ---
# NOTE: GEMINI_API_KEY must be set in Render environment variables
GEMINI_MODEL = "gemini-2.5-flash" 
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"

# ======== Categories (Final LLM Target List) ========
# The LLM will be guided to choose one of these categories based on prefix and context.
CATEGORIES = {
    # USA CATEGORIES (Targeted) 
    # NOTE: USA News is strictly local (Chicago/Illinois). 
    "USA News": ["chicago", "illinois", "chgo"], 
    "USA Movies": ["hbo", "cinemax", "starz", "amc", "showtime", "tcm", "movie", "films", "cine", "mgm", "indieplex", "lmn", "lifetime movies"],
    "USA Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids", "disney jr", "cartoonito", "family", "kids"],
    "US LATINO": ["latino", "spanish", "univision", "telemundo"],
    "Documentary": ["nat geo", "discovery", "history", "documentary", "science", "travel", "animal planet", "id", "investigation", "crime", "hgtv", "cooking channel", "food network", "fyi"],
    "Adult": ["xxx", "porn", "adult", "eros"],
    
    # MEXICO CATEGORIES 
    "Mexico News": ["televisa", "tv azteca", "milenio", "imagen", "foro tv", "forotv", "noticias", "news"],
    "Mexico Movies": ["cine", "canal 5", "canal once", "cinema", "peliculas"],
    "Mexico Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids", "infantil", "ninos", "niños", "discovery kids", "cartoonito", "junior", "kids"],
    "Mexico General": ["las estrellas", "azteca uno", "canal 2", "televisa", "azteca", "canal 4", "general"],
    
    # GLOBAL/SPORTS CATEGORIES (US-priority sports only)
    "Sports": ["sports", "football", "baseball", "basketball", "soccer", "tennis", "golf", "fighting", "nba", "nfl", "mlb", "espn"],
    
    # GENERAL/MISC: Catches all remaining US networks not covered above
    "USA General": ["general", "entertainment", "local"],
}

# Define the definitive set of valid categories the LLM must choose from
VALID_CATEGORIES = list(CATEGORIES.keys())

# Acceptable prefixes for initial filtering
ACCEPTABLE_PREFIXES = ('US|', 'MX|', 'MXC|')


# ======== LLM Helper Function (Targeted and Controlled) ========

def get_llm_category(channel_name, api_key, region_prefix):
    """
    Uses Gemini API for targeted categorization (News and US Latino).
    """
    if not api_key:
        return None
    
    if region_prefix == 'US':
        # US channels can go into any US category or the global Sports category
        target_set = [c for c in VALID_CATEGORIES if c not in CATEGORIES["Mexico General"] and c not in CATEGORIES["Mexico Kids"] and c not in CATEGORIES["Mexico News"] and c != "Mexico General"] # Exclude Mexico specific
    else: 
        # MX / MXC streams only go into Mexican categories
        target_set = [c for c in VALID_CATEGORIES if c in CATEGORIES["Mexico General"] or c in CATEGORIES["Mexico Kids"] or c in CATEGORIES["Mexico News"]]
    
    
    target_list_str = f"[{', '.join(target_set)}]"
    
    # --- System Instruction is the Filter/Enforcer ---
    system_prompt = (
        "You are an expert M3U playlist categorizer. Your primary task is to confirm channel category based on content. "
        "Use Google Search to verify the channel's identity. "
        "If the channel is US NEWS, it must ONLY be categorized as 'USA News' if it is local news (Chicago or Illinois). "
        "Otherwise, all national news (CNN, FOX, MSNBC) must be categorized as 'USA General'. "
        "If the channel is US LATINO, confirm its identity and place it in 'US LATINO'. "
        "Output ONLY the single BEST matching group title. You MUST choose one category name from the following list: "
        f"{target_list_str}. Do not provide any explanation, comments, or extra text."
    )
    
    user_query = f"Categorize this channel name: '{channel_name}'"

    url = f"{GEMINI_API_BASE_URL}{GEMINI_MODEL}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "tools": [{"google_search": {} }], 
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "config": {"temperature": 0.1}
    }

    try:
        # Use a short timeout to keep process moving
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=25) 
        response.raise_for_status()
        
        result = response.json()
        
        text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
        
        # Validate that the LLM returned a valid category name
        if text in VALID_CATEGORIES:
            return text
        
        return None
            
    except requests.exceptions.RequestException as e:
        print(f"Gemini API call failed for {channel_name}: {e}")
        return None

# ======== Core Processing Function (LLM-Powered) ========

def stream_and_categorize(lines_iterator, tvg_url=None, api_key=None):
    """
    Generator that processes the M3U line-by-line, using the LLM for primary categorization.
    """
    seen_streams = set()
    
    header = '#EXTM3U'
    if tvg_url:
        header += f' url-tvg="{tvg_url}"'
    yield header + '\n'

    current_ext = None
    
    # Regex for separate words like 'tele' (word boundary needed to exclude 'television')
    tele_word_regex = re.compile(r'\btele\b')

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
            
            # --- 1. Crucial Prefix Filter & Region Determination ---
            if display_upper.startswith('US|'):
                region_prefix = 'US'
                region_categories = US_CATEGORY_NAMES
                fallback_category = "USA General"
            elif display_upper.startswith(('MX|', 'MXC|')):
                region_prefix = 'MX'
                region_categories = MEXICO_CATEGORY_NAMES
                fallback_category = "Mexico General"
            else:
                current_ext = None 
                continue

            # --- 2. De-Duplication Check ---
            if line in seen_streams:
                current_ext = None 
                continue
            
            seen_streams.add(line)

            # --- 3. Keyword Categorization (for stability) ---
            found = None
            for cat_name in region_categories:
                keywords = CATEGORIES.get(cat_name)
                if keywords and any(kw in display_lower for kw in keywords):
                    found = cat_name
                    break
            
            # --- 4. Targeted LLM Check (News and Latino Only) ---
            
            # Define keywords that trigger the LLM for inspection
            needs_llm_check = False
            if region_prefix == 'US':
                # Check for News or Latino keywords
                if ('news' in display_lower) or ('noticias' in display_lower) or ('programa' in display_lower) or \
                   ('canal' in display_lower) or ('novela' in display_lower) or tele_word_regex.search(display_lower) or \
                   ('latino' in display_lower) or ('spanish' in display_lower):
                    needs_llm_check = True

            if not found and needs_llm_check:
                llm_category = get_llm_category(display_name, api_key, region_prefix)
                if llm_category:
                    found = llm_category

            # --- 5. Final Category Assignment and Prefix Removal ---
            
            final_group = found if found else fallback_category

            new_display_name = display_name
            for prefix in ACCEPTABLE_PREFIXES:
                if new_display_name.upper().startswith(prefix):
                    new_display_name = new_display_name[len(prefix):].lstrip()
                    break

            attributes = display_match.group(1).strip()
            # Note: add_group_title is replaced by direct string formatting for cleanliness
            modified_ext_line = f'{attributes} group-title="{final_group}",{new_display_name}'
            
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
