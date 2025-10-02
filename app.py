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
# The LLM will be forced to choose one of these categories for every US/MX channel.
CATEGORIES = {
    # USA CATEGORIES 
    "USA News": ["chicago", "illinois"], 
    "USA Movies": ["movie", "cinema", "films"],
    "USA Kids": ["kids", "children", "cartoon", "disney", "nickelodeon"],
    "US LATINO": ["latino", "spanish", "univision", "telemundo"],
    "Documentary": ["documentary", "history", "science", "travel"],
    "Adult": ["adult", "xxx"],
    "USA General": ["general", "entertainment", "local"], 
    
    # MEXICO CATEGORIES 
    "Mexico Kids": ["kids", "children", "cartoon", "infantil"],
    "Mexico General": ["general", "news", "movies", "tv"],
    
    # GLOBAL/SPORTS CATEGORY (All sports are combined here)
    "Sports": ["sports", "football", "baseball", "basketball", "soccer", "tennis", "golf", "fighting", "esports"]
}

# Define the definitive set of valid categories the LLM must choose from
VALID_CATEGORIES = list(CATEGORIES.keys())

# Acceptable prefixes for initial filtering
ACCEPTABLE_PREFIXES = ('US|', 'MX|', 'MXC|')

# Define which categories belong to which region for prioritization
US_CATEGORY_NAMES = {"USA News", "USA Movies", "USA Kids", "US LATINO", "Documentary", "Adult", "USA General", "Sports"}
MEXICO_CATEGORY_NAMES = {"Mexico Kids", "Mexico General"}
GLOBAL_CATEGORY_NAMES = {"Sports"}


# ======== LLM Helper Function (Now the primary engine) ========

def get_llm_category(channel_name, api_key, region_prefix):
    """
    Uses Gemini API with Search Grounding to categorize a channel.
    """
    if not api_key:
        return None
    
    # Determine the strict list of categories the LLM must choose from based on the channel's prefix
    if region_prefix == 'US':
        # US channels can go into any US category or the global Sports category
        target_set = [c for c in VALID_CATEGORIES if c not in MEXICO_CATEGORY_NAMES]
    else: # MX / MXC streams only go into Mexican categories
        target_set = MEXICO_CATEGORY_NAMES
    
    target_list_str = f"[{', '.join(target_set)}]"
    
    system_prompt = (
        "You are an expert M3U playlist categorizer. Your task is to accurately assign a category for the provided channel name. "
        "Use Google Search to verify the channel's type, content, and intended market. "
        "Output ONLY the single BEST matching group title. You MUST choose one category name from the following list: "
        f"{target_list_str}. Do not provide any explanation, comments, or extra text."
    )
    
    user_query = f"Categorize this channel: '{channel_name}'"

    url = f"{GEMINI_API_BASE_URL}{GEMINI_MODEL}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "tools": [{"google_search": {}}], 
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "config": {"temperature": 0.1}
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=25) # Short timeout to keep process moving
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
            elif display_upper.startswith(('MX|', 'MXC|')):
                region_prefix = 'MX'
            else:
                current_ext = None 
                continue

            # --- 2. De-Duplication Check ---
            if line in seen_streams:
                current_ext = None 
                continue
            
            seen_streams.add(line)

            # --- 3. Determine Final Category (LLM is the Boss) ---
            
            # 3a. Fallback Category (if LLM fails)
            if region_prefix == 'US':
                fallback_category = "USA General"
            else:
                fallback_category = "Mexico General"

            # 3b. Use LLM
            found_category = get_llm_category(display_name, api_key, region_prefix)

            # --- 4. Final Category Assignment and Prefix Removal ---
            
            final_group = found_category if found_category else fallback_category

            new_display_name = display_name
            for prefix in ACCEPTABLE_PREFIXES:
                if new_display_name.upper().startswith(prefix):
                    new_display_name = new_display_name[len(prefix):].lstrip()
                    break

            attributes = display_match.group(1).strip()
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
