from flask import Flask, Response
import requests
import re
import os

# Initialize the Flask web application
app = Flask(__name__)

# IMPORTANT: A regular expression to reliably split the M3U line into two parts:
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

# ======== Helper Functions (unchanged) ========

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

def parse_and_clean(lines):
    """Iterates through the raw M3U lines and applies category grouping."""
    organized = ['#EXTM3U']
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF'):
            match = EXTINF_REGEX.match(line)
            if not match:
                i += 1
                continue

            ext = line 
            display_name = match.group(2).strip()
            display = display_name.lower()
            stream = lines[i+1].strip() if (i+1) < len(lines) else ''
            found = None
            for cat, keywords in CATEGORIES.items():
                if any(kw in display for kw in keywords):
                    found = cat
                    break
            
            if found:
                new_ext = add_group_title(ext, found)
                organized.append(new_ext)
                organized.append(stream)
            
            i += 2
        else:
            i += 1
    return organized

# ======== Routes (The Web URLs) ========

@app.route("/")
def home():
    """Simple status page."""
    return "The M3U Categorizer is running! Get your updated playlist from /m3u."

@app.route("/m3u")
def get_m3u():
    """Fetches the source M3U using a fallback list of hosts."""
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    
    # Check only for username/password, as hosts are now hardcoded
    if not username or not password:
        return Response("ERROR: Authentication credentials (USERNAME or PASSWORD) are not set.", mimetype="text/plain", status=500)

    # 1. Hardcoded list of IPTV provider hosts for automatic failover
    # The hosts will be tried in this order until one returns a valid M3U file.
    hosts = [
        "http://line.premiumpowers.net",
        "http://servidorgps.org",
        "http://EdgesBuddySad.h1ott.com",
        "http://superberiln24.com"
    ]

    # 2. Loop through the hosts until a successful connection is made
    successful_response = None
    last_error = None
    
    for host in hosts:
        # 3. Construct the full M3U URL for the current host
        # Note: We assume the base path is always '/get.php?'
        m3u_url = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
        print(f"Attempting connection to: {host}") # Log which host we are trying
        
        try:
            # 4. Fetch the playlist with the 300-second timeout
            r = requests.get(m3u_url, timeout=300) 
            r.raise_for_status() # Raises HTTPError for 4xx or 5xx status codes
            
            # If successful and looks like M3U, we stop and use this one
            if r.text.strip().startswith('#EXTM3U'):
                successful_response = r
                break 
            else:
                last_error = f"Host {host} returned content, but it was not a valid M3U."
                print(last_error)

        except requests.exceptions.RequestException as e:
            last_error = f"Host {host} failed with error: {e}"
            print(last_error)
            # Continue to the next host in the list

    # 5. Check the result after looping through all hosts
    if successful_response:
        try:
            # Process the playlist
            lines = successful_response.text.splitlines()
            organized_lines = parse_and_clean(lines)
            
            # Return the newly organized playlist
            return Response("\n".join(organized_lines), mimetype="application/x-mpegurl")
        
        except Exception as e:
            # Handle processing errors after successful fetch
            print(f"Error during M3U processing: {e}")
            return Response(f"Error occurred during M3U processing: {e}", mimetype="text/plain", status=500)

    else:
        # All hosts failed
        print("FATAL: All hosts failed to return a valid M3U file.")
        return Response(f"Error: Could not retrieve a valid M3U from any backup host. Last error was: {last_error}", mimetype="text/plain", status=503)

# ======== Run App (unchanged) ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)
