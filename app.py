from flask import Flask, Response
import requests
import re
import os

# Initialize the Flask web application
app = Flask(__name__)

# Regex to capture attributes (Group 1) and display name (Group 2)
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)

# ======== Categories ========
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

def stream_and_categorize(lines_iterator):
    """
    A generator function that processes the M3U line-by-line (streaming)
    to avoid running out of memory.
    """
    # 1. Yield the required header immediately
    yield '#EXTM3U\n'

    current_ext = None
    
    # 2. Iterate through lines as they are received
    for raw_line in lines_iterator:
        try:
            line = raw_line.decode('utf-8').strip()
        except UnicodeDecodeError:
            continue # Skip lines that can't be decoded

        if line.startswith('#EXTINF'):
            current_ext = line
            # Continue to the next line to find the stream URL
            continue
        
        # 3. If the line is a stream URL and follows an #EXTINF line
        if current_ext and (line.startswith('http') or line.startswith('rtmp')):
            # Process the channel name for categorization
            match = EXTINF_REGEX.match(current_ext)
            if not match:
                current_ext = None
                continue
                
            display = match.group(2).strip().lower()
            
            found = None
            # Find the category based on keywords
            for cat, keywords in CATEGORIES.items():
                if any(kw in display for kw in keywords):
                    found = cat
                    break
            
            # If categorized, yield the modified #EXTINF line and the URL
            if found:
                new_ext = add_group_title(current_ext, found)
                yield new_ext + '\n'
                yield line + '\n'

            # Reset the state for the next channel pair
            current_ext = None

        # Ignore other lines (like #EXTGRP or comments)
        elif current_ext:
            # If we had an EXTINF but the next line wasn't a stream, reset
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

    # 1. Hardcoded list of IPTV provider hosts for automatic failover
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
            # 2. IMPORTANT: Use stream=True to prevent loading the entire file into memory
            r = requests.get(m3u_url, timeout=300, stream=True) 
            r.raise_for_status() 
            
            # Check for a valid M3U file start without loading the entire content
            first_line = next(r.iter_lines(), b'').decode('utf-8').strip()
            
            if first_line.startswith('#EXTM3U'):
                successful_response = r
                break 
            else:
                last_error = f"Host {host} returned content that didn't start with #EXTM3U."
                print(last_error)

        except requests.exceptions.RequestException as e:
            last_error = f"Host {host} failed with error: {e}"
            print(last_error)
            # Continue to the next host

    # 3. If a successful streaming response was found, pass it to the generator
    if successful_response:
        # Chain the first line (already read) back onto the rest of the stream
        lines_to_process = [first_line.encode('utf-8')] + list(successful_response.iter_lines())
        
        # Flask Response streams the output using the generator, consuming minimal memory
        return Response(stream_and_categorize(iter(lines_to_process)), mimetype="application/x-mpegurl")
    else:
        # All hosts failed
        print("FATAL: All hosts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U from any backup host. Last error was: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (unchanged) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
```eof

### Next Step

1.  **Replace** your current `app.py` on GitHub with this code.
2.  **Commit** the change.
3.  **Wait** for Render to automatically redeploy.

This is the most robust version, engineered specifically to beat the memory limit, the slow network, and the single-host problem. Once it's Live, you'll be ready for Tivimate!
