from flask import Flask, Response
import requests
import re
import os
import itertools
import time # Used for retry logic delay

# Initialize the Flask web application
app = Flask(__name__)

# Regex to capture attributes (Group 1) and display name (Group 2)
# NOTE: This only captures attributes up to the comma, the display name is Group 2
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)

# ======== Categories (Customized Final List) ========
CATEGORIES = {
    # USA CATEGORIES (Documentary/Adult are now US-exclusive)
    "USA News": ["cnn", "fox news", "msnbc", "nbc news", "abc news", "cbs news", "chicago", "illinois"],
    "USA Movies": ["hbo", "cinemax", "starz", "amc", "showtime", "tcm", "movie", "christmas"],
    "USA Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids"],
    "US LATINO": ["telemundo", "univision", "uni mas", "unimas", "galavision", "hispana"],
    "Documentary": ["nat geo", "discovery", "history", "documentary"],
    "Adult": ["xxx", "porn", "adult", "eros"],
    
    # MEXICO CATEGORIES
    "Mexico News": ["televisa", "tv azteca", "milenio", "imagen", "foro tv", "forotv"],
    "Mexico Movies": ["cine", "canal 5", "canal once", "cinema"],
    "Mexico Kids": ["canal once niños", "bitme", "kids mexico"],
    "Mexico General": ["las estrellas", "azteca uno", "canal 2", "televisa"],
    
    # GLOBAL CATEGORIES (Sports, eSports - can be US or MX)
    "Basketball": ["nba", "basketball"],
    "Football": ["nfl", "football", "college football", "espn college"],
    "Baseball": ["mlb", "baseball"],
    "Soccer": ["soccer", "futbol", "fútbol", "liga mx", "champions", "premier league", "laliga"],
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
# GLOBAL_CATEGORY_NAMES only includes sports, eSports, etc.
GLOBAL_CATEGORY_NAMES = CATEGORIES.keys() - US_CATEGORY_NAMES - MEXICO_CATEGORY_NAMES


# ======== Core Processing Function (Simplified & Optimized) ========

def stream_and_categorize(lines_iterator):
    """
    Generator that processes the M3U line-by-line, filtering by prefix,
    categorizing using prefix priority, removing duplicates, and stripping prefixes.
    """
    seen_streams = set()
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
            
            display_match = EXTINF_REGEX.match(current_ext)
            if not display_match:
                current_ext = None
                continue

            display_name = display_match.group(2).strip()
            display_upper = display_name.upper()
            display_lower = display_name.lower()
            
            # --- 1. Crucial Prefix Filter ---
            if not display_upper.startswith(ACCEPTABLE_PREFIXES):
                current_ext = None 
                continue

            # --- 2. De-Duplication Check ---
            if line in seen_streams:
                current_ext = None 
                continue
            
            seen_streams.add(line)

            # --- 3. Determine Target Categories based on Prefix ---
            
            if display_upper.startswith('US|'):
                # US streams check US categories (including Documentary/Adult) and Global categories
                target_categories = US_CATEGORY_NAMES.union(GLOBAL_CATEGORY_NAMES)
                is_mexican_stream = False
            elif display_upper.startswith(('MX|', 'MXC|')):
                # Mexican streams check Mexico categories and Global categories (NO US-only groups like Documentary)
                target_categories = MEXICO_CATEGORY_NAMES.union(GLOBAL_CATEGORY_NAMES)
                is_mexican_stream = True
            else:
                current_ext = None 
                continue # Should be caught by prefix filter, but skip anyway

            # --- 4. Categorization Check ---
            found = None
            for cat_name in target_categories:
                keywords = CATEGORIES.get(cat_name)
                if keywords and any(kw in display_lower for kw in keywords):
                    found = cat_name
                    break
            
            # --- 5. Fallback Logic (General Groups) ---
            if not found:
                if is_mexican_stream:
                    found = "Mexico General" 
                else:
                    found = "USA General" 
            
            # --- 6. Final Formatting and Prefix Removal ---
            
            # Strip the prefix from the display name for a clean look
            new_display_name = display_name
            for prefix in ACCEPTABLE_PREFIXES:
                if new_display_name.upper().startswith(prefix):
                    # Strip the prefix and any optional space after it
                    new_display_name = new_display_name[len(prefix):].lstrip()
                    break

            # Extract attributes from current_ext (Group 1 of EXTINF_REGEX)
            attributes = display_match.group(1).strip()
            
            # Rebuild the #EXTINF line with the new, clean display name and group title
            modified_ext_line = f'{attributes} group-title="{found}",{new_display_name}'
            
            # --- 7. Yield the Organized Channel ---
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
    Fetches the source M3U using a retry mechanism for the single good host
    and streams the result to avoid memory issues.
    """
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    
    if not username or not password:
        return Response("ERROR: Authentication credentials (USERNAME or PASSWORD) are not set.", mimetype="text/plain", status=500)

    # Use the only known stable host (with built-in retry logic)
    host = "http://line.premiumpowers.net"
    m3u_url_template = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

    successful_response = None
    lines_to_process = None
    last_error = "Initial attempt failed."
    
    # Robust Retry Loop (up to 5 attempts)
    for attempt in range(5):
        m3u_url = m3u_url_template
        print(f"Attempting connection to: {host} (Attempt {attempt + 1}/5)")
        
        try:
            r = requests.get(m3u_url, timeout=300, stream=True) 
            r.raise_for_status() 
            
            # Read only the first line for verification (memory-safe)
            raw_lines_iterator = r.iter_lines()
            first_line_raw = next(raw_lines_iterator, b'')
            first_line = first_line_raw.decode('utf-8').strip()
            
            if first_line.startswith('#EXTM3U'):
                successful_response = r
                # Chain the first line back with the rest of the stream
                lines_to_process = itertools.chain([first_line_raw], raw_lines_iterator)
                break 
            else:
                last_error = f"Host {host} returned content that didn't start with #EXTM3U."
                print(last_error)

        except requests.exceptions.RequestException as e:
            last_error = f"Host {host} failed with error: {e}"
            print(last_error)
        
        # Wait 5 seconds before the next retry
        time.sleep(5) 

    # Final response preparation
    if successful_response:
        # Flask Response streams the output using the generator
        return Response(stream_and_categorize(lines_to_process), mimetype="application/x-mpegurl")
    else:
        # All attempts failed
        print("FATAL: All attempts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U after 5 retries. Last error was: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (unchanged) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
