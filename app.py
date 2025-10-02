from flask import Flask, Response
import requests
import re
import os
from collections import defaultdict
from datetime import datetime

app = Flask(__name__)

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
    if 'group-title' in extinf_line:
        return re.sub(r'group-title=".*?"', f'group-title="{category}"', extinf_line, count=1)
    m = re.match(r'(#EXTINF:[^\n\r]*?)(,)(.*)', extinf_line)
    if m:
        return f'{m.group(1)} group-title="{category}"{m.group(2)}{m.group(3)}'
    return extinf_line.rstrip('\n') + f' group-title="{category}"\n'

def parse_and_clean(lines):
    organized = ['#EXTM3U']
    for i in range(len(lines)):
        line = lines[i].rstrip('\n')
        if line.strip().startswith('#EXTINF'):
            ext = line
            display = ext.split(',')[-1].strip().lower()
            stream = lines[i+1].rstrip('\n') if (i+1) < len(lines) else ''
            found = None
            for cat, keywords in CATEGORIES.items():
                if any(kw in display for kw in keywords):
                    found = cat
                    break
            if not found:
                continue
            new_ext = add_group_title(ext, found)
            organized.append(new_ext)
            organized.append(stream)
    return organized

# ======== Flask Route ========
@app.route("/m3u")
def get_m3u():
    try:
        username = os.environ.get("USERNAME")
        password = os.environ.get("PASSWORD")
        if not username or not password:
            return Response("USERNAME or PASSWORD not set in environment variables", mimetype="text/plain")

        m3u_url = f"http://line.premiumpowers.net/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
        r = requests.get(m3u_url, timeout=30)
        r.raise_for_status()
        lines = r.text.splitlines()
        organized_lines = parse_and_clean(lines)
        return Response("\n".join(organized_lines), mimetype="text/plain")
    except Exception as e:
        return Response(f"Error fetching playlist: {e}", mimetype="text/plain")

# ======== Run App ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
