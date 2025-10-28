from dotenv import load_dotenv
import os
import base64
from requests import post, get
import requests
import json
from google import genai
import re
import urllib.parse
import time

load_dotenv()

client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")

# Update Functionality (needs frontend?)
auth_code = os.getenv("AUTH_CODE")
refresh_token = os.getenv("REFRESH_TOKEN")

# Configure Gemini API Connection
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Open the JSON genres file for reading
with open('genre-seeds.json', 'r') as file:
    # Load the JSON data from the file into a Python dictionary
    data = json.load(file)
    
    # Access the list associated with the "genres" key
    genres = data['genres']

# Retrieve client/app/server.py token for API calls
def get_client_token():
    auth_string = client_id + ":" + client_secret
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = str(base64.b64encode(auth_bytes), "utf-8")

    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization" : "Basic " + auth_base64,
        "Content-Type" : "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}

    result = post(url, headers=headers, data=data)
    json_result = json.loads(result.content)
    token = json_result["access_token"]
    return token

# Retrieve Spotify user token for private data and control
def get_user_token():
    auth_string = client_id + ":" + client_secret
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = str(base64.b64encode(auth_bytes), "utf-8")

    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": "Basic " + auth_base64,
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": "https://ubx.ph" # Must match the one in your dashboard
    }

    result = post(url, headers=headers, data=data)
    json_result = json.loads(result.content)
    return json_result["access_token"], json_result["refresh_token"]

def refresh_access_token(refresh_token):
    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    r = post(url, headers=headers, data=data)
    return r.json()

# FUNCTION: Get the current user's Spotify ID
def get_user_id(access_token):
    url = "https://api.spotify.com/v1/me"
    headers = get_auth_header(access_token)
    result = get(url, headers=headers)
    json_result = json.loads(result.content)
    return json_result["id"]

# Request Header Constructor
def get_auth_header(token):
    return {"Authorization": "Bearer " + token}

# FUNCTION: Search for an artist and return artist ID
def get_artist_id(token, artist_name):
    url = "https://api.spotify.com/v1/search"
    headers = get_auth_header(token)
    query = f"?q={artist_name}&type=artist&limit=1"  # Retrieve first artist that shows up in search

    query_url = url + query

    result = get(query_url, headers=headers)

    # Reach and return artist ID if it exists
    artist_object = json.loads(result.content)["artists"]["items"]
    if artist_object: return artist_object[0]["id"]

    return "No artist found for: {artist_name}"

# FUNCTION: Retrieve an artist's top tracks
def get_top_tracks(token, artist_id, uri=False):
    url = f"https://api.spotify.com/v1/artists/{artist_id}/top-tracks?country=PH"
    headers = get_auth_header(token)

    result = get(url, headers=headers)

    # Retrieve and return top tracks
    songs_object = json.loads(result.content)["tracks"]

    if uri:
        track_uris = [song["uri"] for song in songs_object]
        return track_uris
    else:
        songs = [song["name"] for song in songs_object]
        return songs

# FUNCTION: Create an Empty Playlist
def create_playlist(access_token, user_id, playlist_name, playlist_description, is_public):
    url = f"https://api.spotify.com/v1/users/{user_id}/playlists"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = json.dumps({
        "name": playlist_name,
        "description": playlist_description,
        "public": is_public
    })

    result = post(url, headers=headers, data=data)
    json_result = json.loads(result.content)
    return json_result["id"] # Return the new playlist's ID

# FUNCTION: Add tracks to a playlist
def add_tracks_to_playlist(access_token, playlist_id, track_uris):
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    headers = get_auth_header(access_token)
    data = json.dumps({
        "uris": track_uris
    })
    
    result = post(url, headers=headers, data=data)
    return result.status_code # Returns 201 if successful

# FUNCTION: Get User's Top Artists
def get_top_artists(access_token):
    url = "https://api.spotify.com/v1/me/top/artists?limit=10"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = get(url, headers=headers)

    if response.status_code == 200:
        # Get the JSON data
        artists_data = response.json()
        # Extract the 'name' from each artist in the 'items' list
        artist_names = [artist['name'] for artist in artists_data['items']]
        return artist_names
    else:
        print("Error:", response.status_code, response.text)
        return None
    
# HELPER: Structuring text response to dictionary
def extract_playlist_data(text: str):
    # Use regex to capture the three bracketed groups
    pattern = r"\[(.*?)\]\s*__\s*\[(.*?)\]\s*__\s*\[(.*?)\]"
    match = re.search(pattern, text)

    if not match:
        raise ValueError("Text does not match expected pattern")

    # Extract title, description, and songs string
    title, description, songs_str = match.groups()

    # Split songs by comma, strip spaces
    raw_songs = [s.strip() for s in songs_str.split(",") if s.strip()]

    # Convert each "Song - Artist" into (song, artist) tuple
    songs = []
    for item in raw_songs:
        if " - " in item:
            song, artist = item.split(" - ", 1)
            songs.append((song.strip(), artist.strip()))
        else:
            songs.append((item.strip(), None))

    # Structured Data
    return {
        "title": title,
        "description": description,
        "songs": songs
    }
    
# FUNCTION: Retrieve recommended track from Gemini based on user's top artists and 'feel' prompt
def get_recommendations(top_artists, prompt):
    client = genai.Client()
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[
            (
                "You are a world-class song-recommending maestro with deep knowledge of modern pop, indie, and mainstream hits. "
                "I will describe how I feel, and you will create a playlist that perfectly matches the mood, "
                "drawing primarily from my top artists: "
            ), 
            str(top_artists),
            (
                ". You may include as few or as many of these artists as you like, "
                "and you may also include songs from other artists with a similar sound, mood, or vibe but keep these at a maximum of 5 songs. "
                "If I ask for specific artists (e.g. 'tracks by Taylor Swift and Jeremy Zucker'), "
                "prioritize them by suggesting over half of the playlist with their songs while still including a few stylistically "
                "compatible tracks unless I explicitly say otherwise. If I mention specific songs or lyrics, ENSURE that these songs are included. "
                "Only recommend songs that actually exist on Spotify. "
                "You will recommend around 20 songs and must strictly follow this format — include the brackets and underscores exactly as shown:\n\n"
                "[Playlist Title] __ [Playlist Description] __ [Song name 1 - Artist Name, Song name 2 - Artist Name, Song name 3 - Artist Name, ... , Song name 15 - Artist Name]\n\n"
                "Now, here’s how I’m feeling: "
            ),
            prompt
        ]
    )
    return extract_playlist_data(response.text)

# FUNCTION: Search for song ids in Spotify given a list of tracks
def get_song_ids(token, tracks):
    """
    Search for a list of (track_name, artist_name) pairs and return their Spotify track URIs.
    Tracks should be a list of tuples or lists: [(track, artist), ...]
    """
    headers = {"Authorization": f"Bearer {token}"}
    song_ids = []
    failed_songs = []

    for i, (track_name, artist_name) in enumerate(tracks, start=1):
        try:
            # Ensure both are strings
            if not isinstance(track_name, str) or not isinstance(artist_name, str):
                raise TypeError(f"Non-string value detected — track: {track_name}, artist: {artist_name}")

            query_track = urllib.parse.quote(track_name)
            query_artist = urllib.parse.quote(artist_name)

            url = f"https://api.spotify.com/v1/search?q=track%3A{query_track}%20artist%3A{query_artist}&type=track&limit=1"
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()
            items = data.get("tracks", {}).get("items", [])
            if not items:
                print(f"⚠️  No results for '{track_name}' — {artist_name}")
                failed_songs.append((track_name, artist_name, "No results"))
                continue

            track_uri = items[0]["uri"]
            song_ids.append(track_uri)
            print(f"✅ ({i}/{len(tracks)}) Found: {track_name} — {artist_name}")

        except TypeError as e:
            print(f"❌ TypeError for '{track_name}' — {artist_name}: {e}")
            failed_songs.append((track_name, artist_name, "TypeError"))
        except requests.RequestException as e:
            print(f"❌ Request failed for '{track_name}' — {artist_name}: {e}")
            failed_songs.append((track_name, artist_name, "Request failed"))
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            print(f"⚠️  Unexpected response for '{track_name}' — {artist_name}: {e}")
            failed_songs.append((track_name, artist_name, "Unexpected response"))

        # Optional delay for rate limit safety
        time.sleep(0.1)

    # Print a summary of any failed songs
    if failed_songs:
        print("\n🚨 Summary of songs with errors:")
        for track, artist, reason in failed_songs:
            print(f"   - {track} — {artist} ({reason})")

    return song_ids

if __name__ == '__main__':
    print("Getting Client Token...")
    client_token = get_client_token()
    print("Refreshing Token...")
    access_token = refresh_access_token(refresh_token)["access_token"]
    print("Retrieving User ID...")
    user_id = get_user_id(access_token)

    # Test Feat 1: Get [Artist] Top Tracks
    # artist_id = get_artist_id(client_token, "Joji")
    # songs = get_top_tracks(client_token, artist_id, True)
    # print(songs)

    # Test Feat 2: Create Empty Playlist (for personal Account)
    # playlist_uri = create_playlist(access_token, user_id, "Joji Playlist", "From Mikylle!", False)

    # Test Feat 3: Add Top Tracks to New Playlist
    # res = add_tracks_to_playlist(access_token, playlist_uri, songs)
    # print(res)

    # Test Feat 4: Retrieve User's Top Artists (ids)
    top_artists = get_top_artists(access_token)
    # print(top_artists)

    # Test Feat 5: Retrieve Recommendations
    print("\n----------START----------")
    prompt = str(input("How are you feeling?\n"))
    print("Generating recommendations...")
    new_playlist = get_recommendations(top_artists, prompt)
    title, description = new_playlist['title'], new_playlist['description']
    print("\n"+title)
    print(description + "\n")
    songs = get_song_ids(client_token, new_playlist['songs'])
    playlist_uri = create_playlist(access_token, user_id, title, description, False)
    add_tracks_to_playlist(access_token, playlist_uri, songs)
    print("\nPlaylist Generation Finished!")
    print("----------END----------")