from dotenv import load_dotenv
import os
import base64
from requests import post, get
import requests
import json
import google.generativeai as genai
import re
import urllib.parse
import time
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from typing import List

# Initialize API
app = FastAPI()

load_dotenv()

client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
redirect_uri = os.getenv("REDIRECT_URI")

# In-memory token storage (for dev/testing)
user_tokens = {}

# Update Functionality (needs frontend?)
auth_code = os.getenv("AUTH_CODE")
refresh_token = os.getenv("REFRESH_TOKEN")

# Configure Gemini API Connection
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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

    if (top_artists):
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
    else:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[
                (
                    "You are a world-class song-recommending maestro with deep knowledge of modern pop, indie, and mainstream hits. "
                    "I will describe how I feel, and you will create a playlist that perfectly matches the mood. "
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
def get_song_uris(token, tracks):
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

# FUNCTION: Search for a set of tracks given a list of IDs
def get_tracks(token: str, song_ids: list[str]):
    """
    Fetches track details for a list of Spotify track IDs.
    """
    headers = {"Authorization": f"Bearer {token}"}
    ids_param = ",".join(song_ids)
    url = f"https://api.spotify.com/v1/tracks?ids={ids_param}"

    response = requests.get(url, headers=headers)

    # Error handling
    if response.status_code != 200:
        print(f"Error fetching tracks: {response.status_code} - {response.text}")
        return []

    data = response.json()
    return data.get("tracks", [])

# ----- Define Endpoints -----

# default endpoint
@app.get("/")
def root():
    return {"Hello": "World"}

# Login to Spotify
@app.get("/login")
def login_to_spotify():
    """
    Redirects user to Spotify authorization page.
    """
    scopes = "playlist-modify-public playlist-modify-private user-top-read"
    auth_url = (
        "https://accounts.spotify.com/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
    )
    return RedirectResponse(url=auth_url)

# Callback to handle Spotify Login
@app.get("/callback")
def spotify_callback(code: str):
    """
    Handles Spotify redirect and exchanges the code for tokens.
    """
    token_url = "https://accounts.spotify.com/api/token"
    auth_string = f"{client_id}:{client_secret}"
    auth_header = base64.b64encode(auth_string.encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri
    }

    response = requests.post(token_url, headers=headers, data=data)

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to get token from Spotify")

    tokens = response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    # Store in memory (dev only)
    user_tokens["current_user"] = {
        "access_token": access_token,
        "refresh_token": refresh_token
    }

    # Option A: return tokens directly to frontend
    return {
        "message": "Spotify login successful",
        "access_token": access_token,
        "refresh_token": refresh_token
    }

# For testing, get locally-saved tokens
def get_current_token():
    """
    Retrieve access token from in-memory store.
    In real use, frontend should pass token in headers.
    """
    token_data = user_tokens.get("current_user")
    if not token_data:
        raise HTTPException(status_code=401, detail="User not logged in")
    return token_data["access_token"]


# Retrieve user's Spotify profile information
@app.get("/me")
def get_spotify_profile(access_token: str = Depends(get_current_token)):
    """
    Example route to get current user's Spotify profile.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get("https://api.spotify.com/v1/me", headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


# Get user's top artists
@app.get("/top-artists")
def return_top_artists(access_token: str = Depends(get_current_token)):
    top_artists = get_top_artists(access_token)
    return top_artists


# Define scheme for incoming JSON data
class PromptRequest(BaseModel):
    prompt: str


# Get recommendations for the user based on prompt
@app.post("/generate-recommendations")
def generate_recommendations(request: PromptRequest):
    prompt = request.prompt

    print("Getting Client Token...")
    client_token = get_client_token()
    print("Refreshing Token...")
    access_token = refresh_access_token(refresh_token)["access_token"]

    top_artists = get_top_artists(access_token)

    print("Generating recommendations...")
    new_playlist = get_recommendations(top_artists, prompt)
    title, description = new_playlist['title'], new_playlist['description']

    song_uris = get_song_uris(client_token, new_playlist['songs'])
    song_ids = []

    # Extract the track IDs safely from URIs
    song_ids = [uri.split(":")[-1] for uri in song_uris if uri.startswith("spotify:track:")]
    
    tracks = get_tracks(client_token, song_ids)

    return {"title": title,
            "description": description,
            "song_uris" : song_uris,
            "tracks" : tracks}

class PlaylistRequest(BaseModel):
    title: str
    description: str
    song_uris: List[str]

@app.post("/generate_playlist")
def generate_playlist(request: PlaylistRequest):
    print("Getting Client Token...")
    client_token = get_client_token()
    print("Refreshing Token...")
    access_token = refresh_access_token(refresh_token)["access_token"]
    print("Retrieving User ID...")
    user_id = get_user_id(access_token)

    playlist_id = create_playlist(access_token, user_id, request.title, request.description, False)
    add_tracks_to_playlist(access_token, playlist_id, request.song_uris)

    return {"message": "Playlist creation completed!", "playlist_id": playlist_id}


# ----- CLI Testing -----
'''
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
    songs = get_song_uris(client_token, new_playlist['songs'])
    playlist_uri = create_playlist(access_token, user_id, title, description, False)
    add_tracks_to_playlist(access_token, playlist_uri, songs)
    print("\nPlaylist Generation Finished!")
    print("----------END----------")
'''