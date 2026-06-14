"""
Daily puzzle generator for CineClue.

Picks a popular movie, builds 5 clues (Director, Genre, Year, Actor, Actor),
and upserts the result into Supabase `daily_puzzles`.

Run manually:  python puzzle_generator.py
Runs via GitHub Actions: .github/workflows/daily_puzzle.yml
"""

import os, random, datetime, requests
from dotenv import load_dotenv

load_dotenv()

TMDB_KEY      = os.environ["TMDB_API_KEY"]
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_SERVICE_KEY"]  # service role key for writes
TMDB_BASE     = "https://api.themoviedb.org/3"
TMDB_IMG      = "https://image.tmdb.org/t/p/w342"

# Popularity thresholds — keeps answers well-known
MIN_VOTE_COUNT  = 10000
MIN_POPULARITY  = 30
# Clue hint movies must still be recognisable
HINT_MIN_VOTES        = 5000
# Actors used as clues must have appeared in 2+ well-voted movies
ACTOR_MIN_KNOWN_MOVIES = 2

SUPERHERO_KEYWORD_ID = 9715  # TMDB keyword: "superhero"

CLUE_ORDER = ["YEAR", "GENRE", "ACTOR", "ACTOR", "DIRECTOR"]

# ─── TMDB helpers ─────────────────────────────────────────────────────────────

def tmdb(path, **params):
    # TMDB discover uses dot notation for comparisons (e.g. vote_count.gte)
    for underscore, dot in [("vote_count_gte", "vote_count.gte"),
                             ("vote_average_gte", "vote_average.gte"),
                             ("popularity_gte", "popularity.gte")]:
        if underscore in params:
            params[dot] = params.pop(underscore)
    params["api_key"] = TMDB_KEY
    r = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def poster_url(path):
    return TMDB_IMG + path if path else None

def get_credits(movie_id):
    return tmdb(f"/movie/{movie_id}/credits")

def get_movie_details(movie_id):
    return tmdb(f"/movie/{movie_id}", append_to_response="credits,keywords")

def is_superhero(details):
    kw_ids = {kw["id"] for kw in details.get("keywords", {}).get("keywords", [])}
    return SUPERHERO_KEYWORD_ID in kw_ids

# ─── Popular movie pool ────────────────────────────────────────────────────────

def fetch_popular_pool(pages=10):
    """Fetch a pool of popular movies suitable as answers."""
    movies = []
    for page in range(1, pages + 1):
        data = tmdb("/discover/movie",
                    sort_by="popularity.desc",
                    vote_count_gte=MIN_VOTE_COUNT,
                    popularity_gte=MIN_POPULARITY,
                    with_original_language="en",
                    without_keywords=SUPERHERO_KEYWORD_ID,
                    page=page)
        movies.extend(data.get("results", []))
    return movies

# ─── Already-used answers ──────────────────────────────────────────────────────

def puzzle_exists(date_str):
    """Return True if a puzzle already exists for this date."""
    url = f"{SUPABASE_URL}/rest/v1/daily_puzzles?puzzle_date=eq.{date_str}&select=puzzle_date"
    r = requests.get(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }, timeout=10)
    r.raise_for_status()
    return len(r.json()) > 0

def fetch_used_ids():
    """Return set of answer_tmdb_id already stored in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/daily_puzzles?select=answer_tmdb_id"
    r = requests.get(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }, timeout=10)
    r.raise_for_status()
    return {row["answer_tmdb_id"] for row in r.json()}

# ─── Clue builders ─────────────────────────────────────────────────────────────

def find_director_clue(movie_id, answer_id, credits):
    directors = [c for c in credits.get("crew", []) if c["job"] == "Director"]
    if not directors:
        return None
    director = directors[0]
    # Use person filmography to guarantee hint was actually directed by them
    data = tmdb(f"/person/{director['id']}/movie_credits")
    directed = [m for m in data.get("crew", [])
                if m.get("job") == "Director" and m["id"] != answer_id
                and m.get("vote_count", 0) >= 100]
    if not directed:
        return None
    directed.sort(key=lambda m: m.get("vote_count", 0), reverse=True)
    for m in directed[:10]:
        kw_data = tmdb(f"/movie/{m['id']}/keywords")
        kw_ids = {kw["id"] for kw in kw_data.get("keywords", [])}
        if SUPERHERO_KEYWORD_ID not in kw_ids:
            return {"category": "DIRECTOR", "connection": director["name"],
                    "hint_tmdb_id": m["id"],
                    "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}
    return None

def find_genre_clue(answer_id, genres):
    if not genres:
        return None
    # Skip Drama (18) — too broad/common to be a useful clue; fall back only if no other genre
    BROAD_GENRE_IDS = {18}
    clue_genre = next((g for g in genres if g["id"] not in BROAD_GENRE_IDS), genres[0])
    data = tmdb("/discover/movie",
                with_genres=clue_genre["id"],
                sort_by="vote_count.desc",
                vote_count_gte=HINT_MIN_VOTES,
                with_original_language="en",
                without_keywords=SUPERHERO_KEYWORD_ID,
                page=1)
    candidates = [m for m in data.get("results", []) if m["id"] != answer_id]
    if not candidates:
        return None
    m = random.choice(candidates[:20])
    return {"category": "GENRE", "connection": clue_genre["name"],
            "hint_tmdb_id": m["id"],
            "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}

def find_year_clue(answer_id, release_date):
    if not release_date:
        return None
    year = release_date[:4]
    data = tmdb("/discover/movie",
                primary_release_year=year,
                sort_by="vote_count.desc",
                vote_count_gte=HINT_MIN_VOTES,
                with_original_language="en",
                without_keywords=SUPERHERO_KEYWORD_ID,
                page=1)
    candidates = [m for m in data.get("results", []) if m["id"] != answer_id]
    if not candidates:
        return None
    m = random.choice(candidates[:10])
    return {"category": "YEAR", "connection": year,
            "hint_tmdb_id": m["id"],
            "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}

def actor_is_recognizable(actor_id):
    """Return True if actor has appeared in 2+ movies with MIN_VOTE_COUNT votes."""
    data = tmdb(f"/person/{actor_id}/movie_credits")
    big = [m for m in data.get("cast", []) if m.get("vote_count", 0) >= MIN_VOTE_COUNT]
    return len(big) >= ACTOR_MIN_KNOWN_MOVIES

def find_actor_clue(answer_id, credits, exclude_ids, lead_only=False):
    cast = [c for c in credits.get("cast", []) if c["id"] not in exclude_ids]
    cast.sort(key=lambda c: c.get("order", 99))
    pool = cast[:3] if lead_only else cast[1:8]
    pool = [c for c in pool if actor_is_recognizable(c["id"])]
    for actor in pool:
        data = tmdb("/discover/movie",
                    with_cast=actor["id"],
                    sort_by="vote_count.desc",
                    vote_count_gte=HINT_MIN_VOTES,
                    with_original_language="en",
                    without_keywords=SUPERHERO_KEYWORD_ID,
                    page=1)
        candidates = [m for m in data.get("results", []) if m["id"] != answer_id]
        if candidates:
            m = random.choice(candidates[:8])
            exclude_ids.add(actor["id"])
            return {"category": "ACTOR", "connection": actor["name"],
                    "hint_tmdb_id": m["id"],
                    "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}
    return None

# ─── Build full puzzle ─────────────────────────────────────────────────────────

def build_puzzle(movie, used_hint_ids=None, force=False):
    """Build a 5-clue puzzle for `movie`. Returns None if clues can't be filled."""
    if used_hint_ids is None:
        used_hint_ids = set()

    details = get_movie_details(movie["id"])

    # Skip sequels / franchise entries (bypass with force=True for manual additions)
    if not force and details.get("belongs_to_collection"):
        return None

    if is_superhero(details):
        return None

    credits = details.get("credits", {})
    genres  = details.get("genres", [])

    used_actor_ids = set()
    clues = []

    builders = [
        lambda: find_year_clue(movie["id"], movie.get("release_date")),
        lambda: find_genre_clue(movie["id"], genres),
        lambda: find_actor_clue(movie["id"], credits, used_actor_ids),           # supporting
        lambda: find_actor_clue(movie["id"], credits, used_actor_ids, lead_only=True),  # lead
        lambda: find_director_clue(movie["id"], movie["id"], credits),
    ]

    for build in builders:
        clue = build()
        if clue is None:
            return None
        # Avoid reusing the same hint movie across clues
        if clue["hint_tmdb_id"] in used_hint_ids:
            return None
        used_hint_ids.add(clue["hint_tmdb_id"])
        clues.append(clue)

    return {
        "answer_tmdb_id":  movie["id"],
        "answer_title":    movie["title"],
        "answer_poster":   poster_url(movie.get("poster_path")),
        "clues":           clues,
    }

# ─── Supabase upsert ───────────────────────────────────────────────────────────

def upsert_puzzle(date_str, puzzle_data):
    payload = {"puzzle_date": date_str, **puzzle_data}
    auth = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

    # Remove existing record for this date before inserting
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/daily_puzzles?puzzle_date=eq.{date_str}",
        headers=auth, timeout=10,
    )

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/daily_puzzles",
        json=payload,
        headers={**auth, "Content-Type": "application/json"},
        timeout=10,
    )
    r.raise_for_status()
    print(f"[OK] Puzzle for {date_str} saved: {puzzle_data['answer_title']}")

# ─── Main ──────────────────────────────────────────────────────────────────────

def generate_for_date(date_str, used_ids, pool, current_year):
    """Try to generate and save a puzzle for date_str. Returns True on success."""
    print(f"Generating puzzle for {date_str}…")
    random.shuffle(pool)
    for movie in pool:
        if movie["id"] in used_ids:
            continue
        if (movie.get("release_date") or "")[:4] == current_year:
            continue
        print(f"  Trying: {movie['title']} ({movie.get('release_date','')[:4]})")
        try:
            puzzle = build_puzzle(movie)
        except Exception as e:
            print(f"    [skip] ({e})")
            continue
        if puzzle:
            upsert_puzzle(date_str, puzzle)
            used_ids.add(movie["id"])
            return True
    print(f"[FAIL] Could not find a suitable movie for {date_str}.")
    return False


def main():
    today        = datetime.date.today()
    current_year = str(today.year)
    used_ids     = fetch_used_ids()
    pool         = fetch_popular_pool(pages=15)

    # Fill any gaps from the past 3 days before generating tomorrow
    for offset in range(-3, 0):
        d = today + datetime.timedelta(days=offset)
        if not puzzle_exists(d.isoformat()):
            print(f"[CATCH-UP] Missing puzzle detected for {d} — filling in.")
            generate_for_date(d.isoformat(), used_ids, pool, current_year)

    # Generate tomorrow's puzzle
    tomorrow = today + datetime.timedelta(days=1)
    if puzzle_exists(tomorrow.isoformat()):
        print(f"[SKIP] Puzzle for {tomorrow} already exists — not overwriting.")
        return
    generate_for_date(tomorrow.isoformat(), used_ids, pool, current_year)

if __name__ == "__main__":
    main()
