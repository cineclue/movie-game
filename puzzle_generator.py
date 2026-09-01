"""
Daily puzzle generator for CineClue.

Picks a popular movie, builds 5 clues (Director, Genre, Year, Actor, Actor),
and upserts the result into Supabase `daily_puzzles`.

A puzzle must be produced every run. generate_for_date() tries progressively
looser filter tiers (GENERATION_TIERS) until one succeeds, so a thin pool of
recognizable candidates for a given day never results in a skipped puzzle.

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

SUPERHERO_KEYWORD_ID = 9715  # TMDB keyword: "superhero"

CLUE_ORDER = ["YEAR", "GENRE", "ACTOR", "ACTOR", "DIRECTOR"]

# ─── Filter tiers ───────────────────────────────────────────────────────────────
# Tier 1 is the normal, well-known-movies-only bar. Each subsequent tier loosens
# vote/recognizability thresholds and eventually allows franchise/superhero/
# current-year movies, so the final tier will match almost anything TMDB has.
DEFAULT_SETTINGS = dict(
    min_vote_count=8000,          # answer + pool popularity floor
    hint_min_votes=5000,          # clue hint movies must clear this
    actor_min_known_movies=2,     # actor must appear in 2+ well-voted movies
    min_actor_popularity=3,       # actor's own TMDB popularity score floor
    director_min_votes=100,       # director's other films must clear this
    max_hint_billing=10,          # actor must be top-N billed in the hint movie
    exclude_superhero=True,
    exclude_franchise=True,
    exclude_current_year=True,
    pool_pages=25,
)

GENERATION_TIERS = [
    DEFAULT_SETTINGS,
    dict(DEFAULT_SETTINGS, min_vote_count=4000, hint_min_votes=2000,
         actor_min_known_movies=2, min_actor_popularity=2, director_min_votes=50,
         max_hint_billing=15, pool_pages=25),
    dict(DEFAULT_SETTINGS, min_vote_count=1500, hint_min_votes=800,
         actor_min_known_movies=1, min_actor_popularity=1, director_min_votes=20,
         max_hint_billing=20, exclude_superhero=False, exclude_franchise=False,
         pool_pages=30),
    dict(DEFAULT_SETTINGS, min_vote_count=300, hint_min_votes=150,
         actor_min_known_movies=1, min_actor_popularity=0.3, director_min_votes=5,
         max_hint_billing=30, exclude_superhero=False, exclude_franchise=False,
         exclude_current_year=False, pool_pages=30),
    dict(DEFAULT_SETTINGS, min_vote_count=0, hint_min_votes=0,
         actor_min_known_movies=0, min_actor_popularity=0, director_min_votes=0,
         max_hint_billing=9999, exclude_superhero=False, exclude_franchise=False,
         exclude_current_year=False, pool_pages=40),
]

# ─── TMDB helpers ─────────────────────────────────────────────────────────────

def tmdb(path, **params):
    # TMDB discover uses dot notation for comparisons (e.g. vote_count.gte)
    for underscore, dot in [("vote_count_gte", "vote_count.gte"),
                             ("vote_average_gte", "vote_average.gte")]:
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

def fetch_popular_pool(pages=10, settings=DEFAULT_SETTINGS):
    """Fetch a pool of popular movies suitable as answers."""
    movies = []
    for page in range(1, pages + 1):
        params = dict(sort_by="popularity.desc",
                      vote_count_gte=settings["min_vote_count"],
                      with_original_language="en",
                      page=page)
        if settings.get("exclude_superhero", True):
            params["without_keywords"] = SUPERHERO_KEYWORD_ID
        data = tmdb("/discover/movie", **params)
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

def hint_is_franchise(movie_id, settings=DEFAULT_SETTINGS):
    """Hint movies should be standalone, like the answer — not sequels/franchise entries."""
    exclude_franchise = settings.get("exclude_franchise", True)
    exclude_superhero = settings.get("exclude_superhero", True)
    if not exclude_franchise and not exclude_superhero:
        return False
    d = tmdb(f"/movie/{movie_id}", append_to_response="keywords")
    if exclude_franchise and d.get("belongs_to_collection"):
        return True
    if exclude_superhero:
        kw_ids = {kw["id"] for kw in d.get("keywords", {}).get("keywords", [])}
        if SUPERHERO_KEYWORD_ID in kw_ids:
            return True
    return False

def find_director_clue(movie_id, answer_id, credits, settings=DEFAULT_SETTINGS):
    directors = [c for c in credits.get("crew", []) if c["job"] == "Director"]
    if not directors:
        return None
    director = directors[0]
    # Use person filmography to guarantee hint was actually directed by them
    data = tmdb(f"/person/{director['id']}/movie_credits")
    directed = [m for m in data.get("crew", [])
                if m.get("job") == "Director" and m["id"] != answer_id
                and m.get("vote_count", 0) >= settings["director_min_votes"]]
    if not directed:
        return None
    directed.sort(key=lambda m: m.get("vote_count", 0), reverse=True)
    for m in directed[:10]:
        if not hint_is_franchise(m["id"], settings):
            return {"category": "DIRECTOR", "connection": director["name"],
                    "hint_tmdb_id": m["id"],
                    "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}
    return None

def find_genre_clue(answer_id, genres, settings=DEFAULT_SETTINGS):
    if not genres:
        return None
    # Skip Drama (18) — too broad/common to be a useful clue; fall back only if no other genre
    BROAD_GENRE_IDS = {18}
    clue_genre = next((g for g in genres if g["id"] not in BROAD_GENRE_IDS), genres[0])
    params = dict(with_genres=clue_genre["id"],
                  sort_by="vote_count.desc",
                  vote_count_gte=settings["hint_min_votes"],
                  with_original_language="en",
                  page=1)
    if settings.get("exclude_superhero", True):
        params["without_keywords"] = SUPERHERO_KEYWORD_ID
    data = tmdb("/discover/movie", **params)
    candidates = [m for m in data.get("results", []) if m["id"] != answer_id]
    standalone = [m for m in candidates[:20] if not hint_is_franchise(m["id"], settings)]
    if not standalone:
        return None
    m = random.choice(standalone)
    return {"category": "GENRE", "connection": clue_genre["name"],
            "hint_tmdb_id": m["id"],
            "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}

def find_year_clue(answer_id, release_date, settings=DEFAULT_SETTINGS):
    if not release_date:
        return None
    year = release_date[:4]
    # Sort by revenue (not vote_count) so the hint is genuinely famous, not just heavily-voted.
    # Safe to compare raw revenue here since candidates are all from the same year (no inflation skew).
    params = dict(primary_release_year=year,
                  sort_by="revenue.desc",
                  vote_count_gte=settings["hint_min_votes"],
                  with_original_language="en",
                  page=1)
    if settings.get("exclude_superhero", True):
        params["without_keywords"] = SUPERHERO_KEYWORD_ID
    data = tmdb("/discover/movie", **params)
    candidates = [m for m in data.get("results", []) if m["id"] != answer_id]
    standalone = [m for m in candidates[:15] if not hint_is_franchise(m["id"], settings)]
    if not standalone:
        return None
    m = random.choice(standalone)
    return {"category": "YEAR", "connection": year,
            "hint_tmdb_id": m["id"],
            "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}

def actor_is_recognizable(actor_id, settings=DEFAULT_SETTINGS):
    """Return True if actor has appeared in enough movies with min_vote_count votes."""
    needed = settings["actor_min_known_movies"]
    if needed <= 0:
        return True
    data = tmdb(f"/person/{actor_id}/movie_credits")
    big = [m for m in data.get("cast", []) if m.get("vote_count", 0) >= settings["min_vote_count"]]
    return len(big) >= needed

def actor_billing_in_movie(actor_id, movie_id):
    """Return the cast order (0-indexed) of actor in movie, or None if not found."""
    credits = tmdb(f"/movie/{movie_id}/credits")
    for member in credits.get("cast", []):
        if member["id"] == actor_id:
            return member.get("order", 999)
    return None

def find_actor_clue(answer_id, credits, exclude_ids, settings=DEFAULT_SETTINGS, lead_only=False):
    cast = [c for c in credits.get("cast", []) if c["id"] not in exclude_ids]
    cast.sort(key=lambda c: c.get("order", 99))
    # lead pool is cast[:3]; supporting pool starts at 1 (not 3) so a second big name
    # billed at position 1 or 2 isn't walled off just because the lead slot claimed position 0
    pool = cast[:3] if lead_only else cast[1:10]
    pool = [c for c in pool if c.get("popularity", 0) >= settings.get("min_actor_popularity", 0)
            and actor_is_recognizable(c["id"], settings)]
    for actor in pool:
        params = dict(with_cast=actor["id"],
                      sort_by="vote_count.desc",
                      vote_count_gte=settings["hint_min_votes"],
                      with_original_language="en",
                      page=1)
        if settings.get("exclude_superhero", True):
            params["without_keywords"] = SUPERHERO_KEYWORD_ID
        data = tmdb("/discover/movie", **params)
        candidates = [m for m in data.get("results", []) if m["id"] != answer_id]
        for m in candidates[:12]:
            billing = actor_billing_in_movie(actor["id"], m["id"])
            if billing is not None and billing < settings["max_hint_billing"] \
                    and not hint_is_franchise(m["id"], settings):
                exclude_ids.add(actor["id"])
                return {"category": "ACTOR", "connection": actor["name"],
                        "hint_tmdb_id": m["id"],
                        "hint_title": m["title"], "poster_url": poster_url(m.get("poster_path"))}
    return None

# ─── Build full puzzle ─────────────────────────────────────────────────────────

def build_puzzle(movie, used_hint_ids=None, force=False, settings=DEFAULT_SETTINGS):
    """Build a 5-clue puzzle for `movie`. Returns None if clues can't be filled."""
    if used_hint_ids is None:
        used_hint_ids = set()

    details = get_movie_details(movie["id"])

    # Skip sequels / franchise entries (bypass with force=True for manual additions,
    # or automatically once a generation tier sets exclude_franchise=False)
    if not force and settings.get("exclude_franchise", True) and details.get("belongs_to_collection"):
        return None

    if settings.get("exclude_superhero", True) and is_superhero(details):
        return None

    credits = details.get("credits", {})
    genres  = details.get("genres", [])

    used_actor_ids = set()

    # Resolve the lead actor first so the supporting pick (which now overlaps cast[1:3])
    # naturally excludes whichever top-3 actor the lead slot already claimed. Built in
    # build_order but re-assembled in display_order so the supporting card still shows first.
    build_order   = ["YEAR", "GENRE", "ACTOR_LEAD", "ACTOR_SUPPORTING", "DIRECTOR"]
    display_order = ["YEAR", "GENRE", "ACTOR_SUPPORTING", "ACTOR_LEAD", "DIRECTOR"]
    builders = {
        "YEAR":             lambda: find_year_clue(movie["id"], movie.get("release_date"), settings),
        "GENRE":            lambda: find_genre_clue(movie["id"], genres, settings),
        "ACTOR_LEAD":       lambda: find_actor_clue(movie["id"], credits, used_actor_ids, settings, lead_only=True),
        "ACTOR_SUPPORTING": lambda: find_actor_clue(movie["id"], credits, used_actor_ids, settings),
        "DIRECTOR":         lambda: find_director_clue(movie["id"], movie["id"], credits, settings),
    }

    resolved = {}
    for slot in build_order:
        clue = builders[slot]()
        if clue is None:
            return None
        # Avoid reusing the same hint movie across clues
        if clue["hint_tmdb_id"] in used_hint_ids:
            return None
        used_hint_ids.add(clue["hint_tmdb_id"])
        resolved[slot] = clue

    clues = [resolved[slot] for slot in display_order]

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

def generate_for_date(date_str, used_ids, current_year):
    """Generate and save a puzzle for date_str, trying GENERATION_TIERS in order.
    Each tier loosens vote/recognizability filters; the last tier allows almost
    any movie, so this only returns False if TMDB itself is unreachable or the
    entire pool has already been used as an answer."""
    print(f"Generating puzzle for {date_str}…")
    for tier_num, settings in enumerate(GENERATION_TIERS, start=1):
        pool = fetch_popular_pool(pages=settings["pool_pages"], settings=settings)
        random.shuffle(pool)
        print(f"  [tier {tier_num}/{len(GENERATION_TIERS)}] "
              f"min_votes={settings['min_vote_count']} hint_votes={settings['hint_min_votes']} "
              f"pool={len(pool)}")
        for movie in pool:
            if movie["id"] in used_ids:
                continue
            if settings["exclude_current_year"] and (movie.get("release_date") or "")[:4] == current_year:
                continue
            print(f"    Trying: {movie['title']} ({movie.get('release_date','')[:4]})")
            try:
                puzzle = build_puzzle(movie, force=not settings["exclude_franchise"], settings=settings)
            except Exception as e:
                print(f"      [skip] ({e})")
                continue
            if puzzle:
                upsert_puzzle(date_str, puzzle)
                used_ids.add(movie["id"])
                return True
        print(f"  [tier {tier_num} exhausted] no suitable movie found, loosening further…")
    print(f"[FAIL] Exhausted every tier for {date_str} — no candidates left at all.")
    return False


def main():
    today        = datetime.date.today()
    current_year = str(today.year)
    used_ids     = fetch_used_ids()
    failures     = []

    # Fill any gaps from the past 3 days, and today itself, before generating tomorrow
    for offset in range(-3, 1):
        d = today + datetime.timedelta(days=offset)
        if not puzzle_exists(d.isoformat()):
            print(f"[CATCH-UP] Missing puzzle detected for {d} — filling in.")
            if not generate_for_date(d.isoformat(), used_ids, current_year):
                failures.append(d.isoformat())

    # Generate tomorrow's puzzle
    tomorrow = today + datetime.timedelta(days=1)
    if puzzle_exists(tomorrow.isoformat()):
        print(f"[SKIP] Puzzle for {tomorrow} already exists — not overwriting.")
    elif not generate_for_date(tomorrow.isoformat(), used_ids, current_year):
        failures.append(tomorrow.isoformat())

    # Non-zero exit marks the Actions step failed, which triggers the Pushover
    # alert step in daily_puzzle.yml (also fires on any unhandled crash, e.g.
    # TMDB being unreachable — that raises before this point ever runs).
    if failures:
        raise SystemExit(f"Failed to generate puzzle(s) for: {', '.join(failures)}")

if __name__ == "__main__":
    main()
