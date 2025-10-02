# Filename: app.py
import requests
import re
from collections import defaultdict
from flask import Flask, Response
import os

# ======== M3U URL (using your login) ========
M3U_URL = f"http://line.premiumpowers.net/get.php?username={os.environ['USERNAME']}&password={os.environ['PASSWORD']}&type=m3u_plus&output=ts"

# ======== Categories ========
CATEGORIES = {
    # USA
    "USA News": ["cnn", "fox news", "msnbc", "nbc news", "abc news", "cbs news"],
    "USA Movies": ["hbo", "cinemax", "starz", "amc", "showtime", "tcm", "movie"],
    "USA Kids": ["cartoon", "nick", "disney", "boomerang", "pbskids"],
    "USA General": ["abc", "nbc", "cbs", "fox", "pbs"],

    # Mexico
    "Mexico News": ["televisa", "tv azteca", "milenio", "imagen", "foro tv", "forotv"],
    "Mexico Movies": ["cine", "canal 5", "canal once", "cinema"],
    "Mexico Kids": ["canal once niños", "bitme", "kids mexico"],
    "Mexico General": ["las estrellas", "azteca uno", "canal 2", "televisa"],

    # Sports by type
    "Basketball": ["nba", "basketball"],
    "Football": ["nfl", "football", "college football", "espn college"],
    "Baseball": ["mlb", "baseball"],
    "Soccer": ["soccer", "futbol", "fútbol", "liga mx", "champions", "premier league", "laliga"],
    "Tennis": ["tennis", "atp", "wta"],
    "Golf": ["golf", "pga"],
    "Fighting": ["ufc", "boxing", "mma", "wwe", "fight"],
    "eSports": ["esports", "gaming", "twitch"],

    # Other useful categories
    "Music": ["mtv", "vh1", "music", "radio"],
    "Documentary": ["nat geo", "discovery", "history", "documentary"],
    "Adult": ["xxx", "porn", "adult", "eros"]
}

def add_group_title(extinf_line, category):
    if 'group-title' in extinf_line:
        return re.sub(r'group-title=".*?"', f'group-title="{category}"', extinf_line, count=1)
    m = re.match(r'(#EXTINF:[^\n\r]*?)(,)(.*)', extinf_line)
    if m:
        return f'{m.group(1)} group-title="{category}"{m.group(2)}{m.group(3)}'
    return extinf_line.rstrip('\n') + f' group-title="{category}"\n'

def download_playlist(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text

def parse_and_clean(lines):
    organized = ['#EXTM3U']
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip('\n')
        if line.strip().startswith('#EXTINF'):
            ext = line
            display = ext.split(',')[-1].strip().lower()
            stream = lines[i+1].rstrip('\n') if (i+1) < n else ''
            found = None
            for cat, keywords in CATEGORIES.items():
                if any(kw in display for kw in keywords):
                    found = cat
                    break
            if not found:
                i += 2
                continue
            new_ext = add_group_title(ext, found)
            organized.append(new_ext)
            organized.append(stream)
            i += 2
        else:
            i += 1
    return organized

app = Flask(__name__)

@app.route("/m3u")
def serve_m3u():
    text = download_playlist(M3U_URL)
    lines_all = text.splitlines()
    organized_lines = parse_and_clean(lines_all)
    m3u_content = "\n".join(organized_lines)
    return Response(m3u_content, mimetype="audio/x-mpegurl")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
