from flask import Flask, Response
import requests
import re
import os

# Initialize the Flask web application
app = Flask(__name__)

# IMPORTANT: A regular expression to reliably split the M3U line into two parts:
# 1. The #EXTINF attributes (Group 1)
# 2. The Channel Display Name (Group 2)
# This is much safer than just splitting by a comma, which can sometimes appear in channel names.
EXTINF_REGEX = re.compile(r'^(#EXTINF:[^,]*)(?:,)(.*)', re.IGNORECASE)

# ======== Categories ========
# These keywords are matched against the LOWERCased channel name.
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
    """
    Adds or replaces the 'group-title' attribute in the #EXTINF line.
    """
    # 1. Check if group-title already exists
    if 'group-title' in extinf_line.lower():
        # Replace the existing group-title value with the new category
        return re.sub(r'group-title=".*?"', f'group-title="{category}"', extinf_line, count=1, flags=re.IGNORECASE)
    
    # 2. If no group-title, insert it right after the attributes but before the channel name comma
    match = EXTINF_REGEX.match(extinf_line)
    if match:
        attributes = match.group(1).strip()
        display_name = match.group(2).strip()
        
        # Reconstruct: Attributes + new group-title + comma + Display Name
        # We ensure the original comma is replaced after we add the new attribute.
        return f'{attributes} group-title="{category}",{display_name}'

    return extinf_line # Return original line if parsing fails

def parse_and_clean(lines):
    """
    Iterates through the raw M3U lines and applies category grouping.
    """
    organized = ['#EXTM3U']
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Check if the line is the start of a channel entry
        if line.startswith('#EXTINF'):
            # Use the robust regex to parse the line
            match = EXTINF_REGEX.match(line)
            
            if not match:
                i += 1
                continue # Skip invalid line structure

            ext = line # Original #EXTINF line
            
            # The part of the line that contains the channel name
            display_name = match.group(2).strip()
            display = display_name.lower() # Lowercase for keyword matching
            
            # The next line should be the stream URL
            stream = lines[i+1].strip() if (i+1) < len(lines) else ''

            found = None
            # Loop through all categories and check for keywords
            for cat, keywords in CATEGORIES.items():
                if any(kw in display for kw in keywords):
                    found = cat
                    break
            
            # If a category was found, update the line and add the pair to the list
            if found:
                new_ext = add_group_title(ext, found)
                organized.append(new_ext)
                organized.append(stream)
            
            # Skip the #EXTINF line and the URL line (always 2 lines total)
            i += 2
        else:
            # Skip any other line (like #EXTGRP, comments, or blank lines)
            i += 1
            
    return organized

# ======== Routes (The Web URLs) ========

@app.route("/")
def home():
    """Simple status page."""
    return "The M3U Categorizer is running! Get your updated playlist from /m3u."

@app.route("/m3u")
def get_m3u():
    """Fetches the source M3U, cleans and categorizes it, and serves the result."""
    try:
        # 1. Get Authentication Details from the secure environment variables
        # When you host this app, you must set these variables (USERNAME, PASSWORD)
        username = os.environ.get("USERNAME")
        password = os.environ.get("PASSWORD")
        
        if not username or not password:
            # IMPORTANT: Change this message after deployment! 
            return Response("ERROR: Authentication credentials (USERNAME or PASSWORD) are not set. I can't fetch your source playlist.", mimetype="text/plain", status=500)

        # 2. Build the URL for your original IPTV provider
        m3u_url = f"http://line.premiumpowers.net/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
        
        # 3. Fetch the original playlist
        r = requests.get(m3u_url, timeout=30)
        r.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        # 4. Check if the response is actually an M3U file
        if not r.text.strip().startswith('#EXTM3U'):
            return Response("ERROR: The server returned content but it doesn't look like an M3U file. Check provider URL or credentials.", mimetype="text/plain", status=500)

        # 5. Process the playlist
        lines = r.text.splitlines()
        organized_lines = parse_and_clean(lines)
        
        # 6. Return the newly organized playlist
        return Response("\n".join(organized_lines), mimetype="application/x-mpegurl") # Use correct M3U MIME type
        
    except requests.exceptions.RequestException as e:
        # Handle network or HTTP errors
        print(f"Network or HTTP Error: {e}")
        return Response(f"Error fetching source playlist (Network Issue): {e}", mimetype="text/plain", status=503)
    
    except Exception as e:
        # Handle all other unexpected errors
        print(f"General Error: {e}")
        return Response(f"An unexpected error occurred during processing: {e}", mimetype="text/plain", status=500)

# ======== Run App ========
if __name__ == "__main__":
    # Get the port from the environment, defaulting to 10000. 
    # This is standard practice for hosting platforms like Render or Heroku.
    port = int(os.environ.get("PORT", 10000)) 
    app.run(host="0.0.0.0", port=port)
