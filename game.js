// ─── Config ────────────────────────────────────────────────────────────────
const SUPABASE_URL  = "https://vocsfxosmbpcjvadwddf.supabase.co";
const SUPABASE_ANON = "sb_publishable_gxIgyHUktN-BuChGRy_5YA_6WdeCMiU";
const TMDB_KEY      = "74aa14a014f685118e47f13cfaaabf07";
const TMDB_IMG      = "https://image.tmdb.org/t/p/w342";
const MAX_GUESSES          = 5;
const ANSWER_MIN_VOTES     = 10000;
const HINT_MIN_VOTES       = 5000;
const MIN_ACTOR_POPULARITY = 6;

// Display order: YEAR → GENRE → ACTOR (supporting) → ACTOR (lead) → DIRECTOR
// Stable sort preserves the relative order of the two ACTOR clues.
const CATEGORY_RANK = { YEAR: 0, GENRE: 1, ACTOR: 2, DIRECTOR: 4 };

// ─── State ─────────────────────────────────────────────────────────────────
let puzzle           = null;
let cluesShown       = 1;
let guessCount       = 0;
let selectedMovie    = null;
let gameOver         = false;
let searchTimer      = null;
let isPractice       = false;
let searchSetup      = false;
let countdownInterval = null;

// ─── DOM refs ──────────────────────────────────────────────────────────────
const clueGrid      = document.getElementById("clue-grid");
const searchInput   = document.getElementById("search-input");
const searchResults = document.getElementById("search-results");
const guessBtn      = document.getElementById("guess-btn");
const guessHistory  = document.getElementById("guess-history");
const endScreen     = document.getElementById("end-screen");

// ─── Init ───────────────────────────────────────────────────────────────────
(async () => {
  const today = getTargetDate();
  const saved = loadState(today);

  puzzle = await fetchPuzzle(today);

  if (!puzzle) {
    showNoPuzzle();
    return;
  }

  renderStreak();
  showTutorialIfNew();

  if (saved) {
    restoreState(saved);
  } else {
    renderClues();
  }

  setupSearch();
})();

// ─── Supabase fetch ─────────────────────────────────────────────────────────
async function fetchPuzzle(date) {
  const url = `${SUPABASE_URL}/rest/v1/daily_puzzles?puzzle_date=eq.${date}&select=*`;
  const res = await fetch(url, {
    headers: {
      apikey: SUPABASE_ANON,
      Authorization: `Bearer ${SUPABASE_ANON}`,
    },
  });
  const rows = await res.json();
  if (!rows || rows.length === 0) return null;
  return rows[0];
}

// ─── Render clues ───────────────────────────────────────────────────────────
function renderClues() {
  clueGrid.innerHTML = "";

  const sorted = [...puzzle.clues].sort(
    (a, b) => (CATEGORY_RANK[a.category] ?? 99) - (CATEGORY_RANK[b.category] ?? 99)
  );

  for (let i = 0; i < MAX_GUESSES; i++) {
    const clue = sorted[i];
    const card = document.createElement("div");
    card.className = "clue-card";
    card.dataset.index = i;

    if (i < cluesShown) {
      // Revealed clue
      const label = document.createElement("span");
      label.className = "clue-label";
      label.textContent = categoryLabel(clue.category);

      const img = document.createElement("img");
      img.className = "clue-poster";
      img.alt = clue.hint_title;
      img.onload  = () => img.style.backgroundImage = "none";
      img.onerror = () => img.style.visibility = "hidden";
      img.src = clue.poster_url || "";

      const title = document.createElement("p");
      title.className = "clue-hint-title";
      title.textContent = clue.hint_title;

      card.appendChild(label);
      card.appendChild(img);
      card.appendChild(title);

      // Show the connection answer once the user has guessed past this clue
      if (clue.connection && (gameOver || i < guessCount)) {
        const conn = document.createElement("p");
        conn.className = "clue-connection";
        conn.textContent = connectionLabel(clue);
        card.appendChild(conn);
      }
    } else {
      // Locked clue placeholder
      const placeholder = document.createElement("div");
      placeholder.className = "clue-poster-placeholder";
      placeholder.textContent = "?";

      const label = document.createElement("span");
      label.className = "clue-label";
      label.style.opacity = "0.3";
      label.textContent = categoryLabel(clue.category);

      card.appendChild(label);
      card.appendChild(placeholder);
    }

    clueGrid.appendChild(card);
  }
}

// ─── Search ─────────────────────────────────────────────────────────────────
function setupSearch() {
  if (searchSetup) return;
  searchSetup = true;

  searchInput.addEventListener("input", () => {
    selectedMovie = null;
    guessBtn.disabled = true;
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (q.length < 2) {
      closeDropdown();
      return;
    }
    searchTimer = setTimeout(() => searchTMDB(q), 300);
  });

  searchInput.addEventListener("keydown", (e) => {
    const items = searchResults.querySelectorAll("li");
    const active = searchResults.querySelector("li.active");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = active ? active.nextElementSibling : items[0];
      if (next) { active?.classList.remove("active"); next.classList.add("active"); }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = active ? active.previousElementSibling : items[items.length - 1];
      if (prev) { active?.classList.remove("active"); prev.classList.add("active"); }
    } else if (e.key === "Enter") {
      if (active) active.click();
      else if (selectedMovie) submitGuess();
    } else if (e.key === "Escape") {
      closeDropdown();
    }
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#search-wrapper")) closeDropdown();
  });

  guessBtn.addEventListener("click", submitGuess);
}

async function searchTMDB(query) {
  const url = `https://api.themoviedb.org/3/search/movie?api_key=${TMDB_KEY}&query=${encodeURIComponent(query)}&include_adult=false`;
  const res  = await fetch(url);
  const data = await res.json();
  const sorted = (data.results || []).sort((a, b) => (b.vote_count || 0) - (a.vote_count || 0));
  renderDropdown(sorted);
}

function renderDropdown(results) {
  searchResults.innerHTML = "";
  const top = results.slice(0, 5);
  if (!top.length) { closeDropdown(); return; }

  top.forEach((movie) => {
    const li   = document.createElement("li");
    const img  = document.createElement("img");
    img.src    = movie.poster_path ? TMDB_IMG + movie.poster_path : "";
    img.alt    = "";

    const name = document.createElement("span");
    name.textContent = movie.title;

    const year = document.createElement("span");
    year.className = "result-year";
    year.textContent = (movie.release_date || "").slice(0, 4);

    li.appendChild(img);
    li.appendChild(name);
    li.appendChild(year);

    li.addEventListener("click", () => {
      selectedMovie = {
        id:     movie.id,
        title:  movie.title,
        year:   (movie.release_date || "").slice(0, 4),
        poster: movie.poster_path ? TMDB_IMG + movie.poster_path : "",
      };
      searchInput.value = movie.title;
      guessBtn.disabled = false;
      closeDropdown();
    });

    searchResults.appendChild(li);
  });

  searchResults.classList.add("open");
}

function closeDropdown() {
  searchResults.classList.remove("open");
  searchResults.innerHTML = "";
}

// ─── Submit guess ────────────────────────────────────────────────────────────
function submitGuess() {
  if (!selectedMovie || gameOver) return;

  guessCount++;
  const isCorrect = selectedMovie.id === puzzle.answer_tmdb_id;

  addGuessRow(selectedMovie.title, isCorrect);

  if (isCorrect) {
    endGame(true);
  } else if (guessCount >= MAX_GUESSES) {
    endGame(false);
  } else {
    cluesShown = guessCount + 1;
    renderClues();
  }

  searchInput.value = "";
  selectedMovie = null;
  guessBtn.disabled = true;

  saveState();
}

function addGuessRow(title, correct) {
  const row = document.createElement("div");
  row.className = `guess-row ${correct ? "correct" : "wrong"}`;

  const icon = document.createElement("span");
  icon.className = "guess-icon";
  icon.textContent = correct ? "✅" : "❌";

  const name = document.createElement("span");
  name.className = "guess-name";
  name.textContent = title;

  row.appendChild(icon);
  row.appendChild(name);
  guessHistory.appendChild(row);
}

// ─── End game ────────────────────────────────────────────────────────────────
function endGame(won) {
  gameOver = true;

  document.getElementById("end-emoji").textContent   = won ? "🎉" : "💀";
  document.getElementById("end-title").textContent   = won ? "Nice one!" : "Better luck tomorrow!";
  document.getElementById("end-message").textContent = won
    ? `You got it in ${guessCount} clue${guessCount === 1 ? "" : "s"}!`
    : "The movie was…";
  document.getElementById("end-poster").src          = puzzle.answer_poster || "";
  document.getElementById("end-movie-title").textContent = puzzle.answer_title;

  updateStats(won, getTargetDate());
  updateStreak(won, getTargetDate());

  if (won && typeof confetti === "function") {
    confetti({ particleCount: 120, spread: 90, origin: { y: 0.6 } });
  }

  endScreen.classList.remove("hidden");

  document.getElementById("end-close-btn").onclick = () => endScreen.classList.add("hidden");

  const endStreakEl = document.getElementById("end-streak");
  const { current, best } = loadStreak();
  if (!isPractice && won && current > 0) {
    endStreakEl.textContent = current === 1 ? "🔥 Win streak started!" : `🔥 ${current} win streak${current === best ? " — new best!" : ""}`;
    endStreakEl.style.display = "";
  } else {
    endStreakEl.style.display = "none";
  }

  const shareBtn       = document.getElementById("share-btn");
  const newPracticeBtn = document.getElementById("new-practice-btn");
  const nextPuzzleEl   = document.getElementById("next-puzzle");
  shareBtn.style.display       = isPractice ? "none" : "";
  newPracticeBtn.style.display = "";
  nextPuzzleEl.style.display   = isPractice ? "none" : "";
  shareBtn.onclick       = () => shareResult(won);
  newPracticeBtn.onclick = () => startPractice();

  if (!isPractice) startCountdown();

  // Disable input
  searchInput.disabled = true;
  guessBtn.disabled    = true;

  saveState();

  if (!isPractice && !window.TEST_MODE) {
    recordAndShowCommunityStats(won, getTargetDate());
  } else {
    document.getElementById("community-stats").innerHTML = "";
  }
}

async function recordAndShowCommunityStats(won, date) {
  const statsEl = document.getElementById("community-stats");
  const alreadyKey = `cineclue_recorded_${date}`;

  try {
    let completions, wins;

    if (!localStorage.getItem(alreadyKey)) {
      const res = await fetch(
        `${SUPABASE_URL}/rest/v1/rpc/increment_puzzle_stats`,
        {
          method: "POST",
          headers: {
            "apikey": SUPABASE_ANON,
            "Authorization": `Bearer ${SUPABASE_ANON}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ p_date: date, p_won: won }),
        }
      );
      const data = await res.json();
      completions = data[0].completions;
      wins        = data[0].wins;
      localStorage.setItem(alreadyKey, "1");
    } else {
      const res = await fetch(
        `${SUPABASE_URL}/rest/v1/daily_puzzles?puzzle_date=eq.${date}&select=completions,wins`,
        { headers: { "apikey": SUPABASE_ANON, "Authorization": `Bearer ${SUPABASE_ANON}` } }
      );
      const data = await res.json();
      completions = data[0].completions;
      wins        = data[0].wins;
    }

    const winPct = completions > 0 ? Math.round((wins / completions) * 100) : 0;
    statsEl.innerHTML = `
      <p class="community-stat">🏆 ${winPct}% win rate today</p>
    `;
  } catch (e) {
    console.error("Community stats error:", e);
    statsEl.innerHTML = "";
  }
}

// ─── Share ────────────────────────────────────────────────────────────────────
function shareResult(won) {
  const squares = [];
  for (let i = 0; i < MAX_GUESSES; i++) {
    if (i < guessCount - (won ? 1 : 0)) squares.push("🟥");
    else if (won && i === guessCount - 1)  squares.push("🟩");
    else squares.push("⬛");
  }
  const lines = [`🎬 MovieHunch ${localDateString()}`];
  lines.push(won ? `Cracked on clue ${guessCount} of ${MAX_GUESSES}` : "💀 Stumped!");
  lines.push(squares.join(""));
  const { current } = loadStreak();
  if (current > 1) lines.push(`🔥 ${current} win streak`);
  lines.push("https://moviehunch.com");
  const text = lines.join("\n");
  const btn  = document.getElementById("share-btn");
  const confirm = () => {
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Share Result"; }, 2000);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(confirm);
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    confirm();
  }
}

// ─── Stats ────────────────────────────────────────────────────────────────────
function loadStats() {
  const raw = localStorage.getItem("cineclue_stats");
  return raw ? JSON.parse(raw) : { played: 0, won: 0, distribution: {1:0,2:0,3:0,4:0,5:0}, last_date: null };
}

function updateStats(won, date) {
  if (isPractice) return;
  const stats = loadStats();
  if (stats.last_date === date) return;
  stats.played++;
  if (won) {
    stats.won++;
    stats.distribution[guessCount] = (stats.distribution[guessCount] || 0) + 1;
  }
  stats.last_date = date;
  localStorage.setItem("cineclue_stats", JSON.stringify(stats));
}

function openStatsModal() {
  const stats  = loadStats();
  const streak = loadStreak();
  document.getElementById("stat-played").textContent   = stats.played;
  document.getElementById("stat-win-pct").textContent  = stats.played > 0 ? Math.round(stats.won / stats.played * 100) : 0;
  document.getElementById("stat-streak").textContent   = streak.current;
  document.getElementById("stat-best").textContent     = streak.best;

  const container = document.getElementById("stats-distribution");
  container.innerHTML = "";
  const max = Math.max(1, ...Object.values(stats.distribution).map(Number));
  for (let i = 1; i <= MAX_GUESSES; i++) {
    const count = stats.distribution[i] || 0;
    const pct   = count > 0 ? Math.max(8, Math.round(count / max * 100)) : 0;
    const row   = document.createElement("div");
    row.className = "dist-row";
    const label = document.createElement("span");
    label.className = "dist-label";
    label.textContent = i;
    const bar = document.createElement("div");
    bar.className = `dist-bar${count > 0 ? " filled" : ""}`;
    bar.style.width = `${pct}%`;
    bar.textContent = count;
    row.append(label, bar);
    container.appendChild(row);
  }

  document.getElementById("stats-modal").classList.remove("hidden");
}

function showTutorial() {
  const overlay = document.getElementById("tutorial-overlay");
  overlay.classList.remove("hidden");

  function dismiss() {
    overlay.classList.add("hidden");
    localStorage.setItem("cineclue_seen_tutorial", "1");
  }

  document.getElementById("tutorial-got-it").onclick = dismiss;
  overlay.onclick = (e) => { if (e.target === overlay) dismiss(); };
  document.addEventListener("keydown", function onKey(e) {
    if (e.key === "Escape") { dismiss(); document.removeEventListener("keydown", onKey); }
  });
}

function showTutorialIfNew() {
  if (!localStorage.getItem("cineclue_seen_tutorial")) showTutorial();
}

document.getElementById("how-to-play-btn").addEventListener("click", showTutorial);
document.getElementById("stats-btn").addEventListener("click", openStatsModal);
document.getElementById("stats-close-btn").addEventListener("click", () => {
  document.getElementById("stats-modal").classList.add("hidden");
});

// ─── Streak ───────────────────────────────────────────────────────────────────
function loadStreak() {
  const raw = localStorage.getItem("cineclue_streak");
  return raw ? JSON.parse(raw) : { current: 0, best: 0, last_date: null };
}

function updateStreak(won, date) {
  if (isPractice) return;
  const streak = loadStreak();
  if (streak.last_date === date) return; // already recorded for this puzzle
  if (won) {
    streak.current++;
    if (streak.current > streak.best) streak.best = streak.current;
  } else {
    streak.current = 0;
  }
  streak.last_date = date;
  localStorage.setItem("cineclue_streak", JSON.stringify(streak));
  renderStreak();
}

function renderStreak() {
  const el = document.getElementById("streak-display");
  if (!el) return;
  const { current, best } = loadStreak();
  if (current > 0) {
    el.textContent = `🔥 ${current}`;
    el.title = `Best streak: ${best}`;
    el.style.display = "";
  } else {
    el.style.display = "none";
  }
}

// ─── Persist state ────────────────────────────────────────────────────────────
function saveState() {
  if (isPractice) return;
  const state = { guessCount, cluesShown, gameOver, guesses: [] };
  guessHistory.querySelectorAll(".guess-row").forEach((r) => {
    state.guesses.push({ title: r.querySelector(".guess-name").textContent, correct: r.classList.contains("correct") });
  });
  localStorage.setItem(`cineclue_${localDateString()}`, JSON.stringify(state));
}

function loadState(date) {
  const raw = localStorage.getItem(`cineclue_${date}`);
  return raw ? JSON.parse(raw) : null;
}

function restoreState(saved) {
  guessCount = saved.guessCount;
  cluesShown = saved.cluesShown;
  gameOver   = saved.gameOver;

  renderClues();

  saved.guesses.forEach((g) => addGuessRow(g.title, g.correct));

  if (gameOver) {
    const won = saved.guesses.at(-1)?.correct ?? false;
    endGame(won);
  } else {
    setupSearch();
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function localDateString() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function getTargetDate() {
  const param = new URLSearchParams(window.location.search).get("date");
  if (param && /^\d{4}-\d{2}-\d{2}$/.test(param)) return param;
  return localDateString();
}

function startCountdown() {
  if (countdownInterval) clearInterval(countdownInterval);
  const el = document.getElementById("next-puzzle-countdown");
  if (!el) return;
  function tick() {
    const now      = new Date();
    const midnight = new Date(now);
    midnight.setDate(midnight.getDate() + 1);
    midnight.setHours(0, 0, 0, 0);
    const diff = midnight - now;
    const h = Math.floor(diff / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    el.textContent = `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  }
  tick();
  countdownInterval = setInterval(tick, 1000);
}

// ─── Practice mode ────────────────────────────────────────────────────────────
document.getElementById("practice-btn").addEventListener("click", startPractice);

async function startPractice() {
  if (puzzle && !gameOver) {
    const msg = isPractice
      ? "You haven't finished your current practice game. Start a new one?"
      : "You haven't finished today's puzzle. Start a practice game?";
    if (!confirm(msg)) return;
  }
  const btn = document.getElementById("practice-btn");
  btn.textContent = "Loading…";
  btn.disabled = true;

  let practicePuzzle = null;
  for (let i = 0; i < 20; i++) {
    try { practicePuzzle = await buildPracticePuzzle(); } catch (e) { /* retry */ }
    if (practicePuzzle) break;
  }

  btn.textContent = "New Practice";
  btn.disabled = false;

  if (!practicePuzzle) { alert("Couldn't build a practice puzzle — try again."); return; }

  puzzle        = practicePuzzle;
  cluesShown    = 1;
  guessCount    = 0;
  selectedMovie = null;
  gameOver      = false;
  isPractice    = true;

  guessHistory.innerHTML = "";
  endScreen.classList.add("hidden");
  searchInput.disabled   = false;
  searchInput.value      = "";
  guessBtn.disabled      = true;
  document.getElementById("practice-banner").style.display = "block";

  renderClues();
  setupSearch();
}

async function buildPracticePuzzle() {
  // Try curated practice pool from Supabase first
  try {
    const res = await fetch(`${SUPABASE_URL}/rest/v1/practice_puzzles?select=*`, {
      headers: { apikey: SUPABASE_ANON, Authorization: `Bearer ${SUPABASE_ANON}` }
    });
    const pool = await res.json();
    if (Array.isArray(pool) && pool.length) {
      return pool[Math.floor(Math.random() * pool.length)];
    }
  } catch (e) { /* fall through to dynamic */ }

  const page = Math.ceil(Math.random() * 20);
  const pool  = await tmdbFetch("/discover/movie", {
    sort_by: "vote_count.desc", vote_count_gte: ANSWER_MIN_VOTES,
    popularity_gte: 30, with_original_language: "en", page,
  });
  const candidates = (pool.results || []).filter(m =>
    (m.release_date || "").slice(0, 4) !== String(new Date().getFullYear())
  );
  if (!candidates.length) return null;
  const movie = candidates[Math.floor(Math.random() * candidates.length)];

  const details = await tmdbFetch(`/movie/${movie.id}`, { append_to_response: "credits" });

  // Skip franchise/sequel films (same rule as server-side generator)
  if (details.belongs_to_collection) return null;

  const credits = details.credits || {};
  const genres  = details.genres  || [];

  const clues        = [];
  const usedIds      = new Set([movie.id]);
  const usedActorIds = new Set();
  const cast         = (credits.cast || []).sort((a, b) => (a.order || 99) - (b.order || 99));

  // 1 — Year
  if (details.release_date) {
    const year = details.release_date.slice(0, 4);
    const d = await tmdbFetch("/discover/movie", {
      primary_release_year: year,
      sort_by: "vote_count.desc", vote_count_gte: HINT_MIN_VOTES,
      with_original_language: "en", page: 1,
    });
    const c = (d.results || []).filter(m => !usedIds.has(m.id));
    if (c.length) {
      const m = c[Math.floor(Math.random() * Math.min(10, c.length))];
      usedIds.add(m.id);
      clues.push({ ...makeClue("YEAR", m), connection: year });
    }
  }

  // 2 — Genre (skip Drama ID=18 — too broad/common to be a useful clue)
  if (genres.length) {
    const BROAD_GENRE_IDS = new Set([18]);
    const clueGenre = genres.find(g => !BROAD_GENRE_IDS.has(g.id)) || genres[0];
    const d = await tmdbFetch("/discover/movie", {
      with_genres: clueGenre.id, sort_by: "vote_count.desc",
      vote_count_gte: HINT_MIN_VOTES, with_original_language: "en", page: 1,
    });
    const c = (d.results || []).filter(m => !usedIds.has(m.id));
    if (c.length) {
      const m = c[Math.floor(Math.random() * Math.min(20, c.length))];
      usedIds.add(m.id);
      clues.push({ ...makeClue("GENRE", m), connection: clueGenre.name });
    }
  }

  // 3 — Supporting actor (cast positions 1–7, filtered by popularity)
  for (const actor of cast.slice(1, 8)) {
    if (clues.filter(c => c.category === "ACTOR").length >= 1) break;
    if (usedActorIds.has(actor.id)) continue;
    if ((actor.popularity || 0) < MIN_ACTOR_POPULARITY) continue;
    const d = await tmdbFetch("/discover/movie", {
      with_cast: actor.id, sort_by: "vote_count.desc",
      vote_count_gte: HINT_MIN_VOTES, with_original_language: "en", page: 1,
    });
    const c = (d.results || []).filter(m => !usedIds.has(m.id));
    if (c.length) {
      const m = c[Math.floor(Math.random() * Math.min(8, c.length))];
      usedIds.add(m.id); usedActorIds.add(actor.id);
      clues.push({ ...makeClue("ACTOR", m), connection: actor.name });
    }
  }

  // 4 — Lead actor (top 3 billed, filtered by popularity)
  for (const lead of cast.slice(0, 3)) {
    if (clues.filter(c => c.category === "ACTOR").length >= 2) break;
    if (usedActorIds.has(lead.id)) continue;
    if ((lead.popularity || 0) < MIN_ACTOR_POPULARITY) continue;
    const d = await tmdbFetch("/discover/movie", {
      with_cast: lead.id, sort_by: "vote_count.desc",
      vote_count_gte: HINT_MIN_VOTES, with_original_language: "en", page: 1,
    });
    const c = (d.results || []).filter(m => !usedIds.has(m.id));
    if (c.length) {
      const m = c[Math.floor(Math.random() * Math.min(8, c.length))];
      usedIds.add(m.id); usedActorIds.add(lead.id);
      clues.push({ ...makeClue("ACTOR", m), connection: lead.name });
    }
  }

  // 5 — Director (use person filmography to guarantee hint was actually directed by them)
  const directors = (credits.crew || []).filter(c => c.job === "Director");
  if (directors.length) {
    const person = await tmdbFetch(`/person/${directors[0].id}/movie_credits`);
    const directed = (person.crew || [])
      .filter(m => m.job === "Director" && !usedIds.has(m.id) && (m.vote_count || 0) >= 100)
      .sort((a, b) => (b.vote_count || 0) - (a.vote_count || 0));
    if (directed.length) {
      const m = directed[0];
      usedIds.add(m.id);
      clues.push({ ...makeClue("DIRECTOR", m), connection: directors[0].name });
    }
  }

  if (clues.length < 5) return null;

  return {
    answer_tmdb_id: movie.id,
    answer_title:   movie.title,
    answer_poster:  movie.poster_path ? TMDB_IMG + movie.poster_path : null,
    clues,
  };
}

function makeClue(category, movie) {
  return {
    category,
    hint_tmdb_id: movie.id,
    hint_title:   movie.title,
    poster_url:   movie.poster_path ? TMDB_IMG + movie.poster_path : null,
  };
}

function connectionLabel(clue) {
  return clue.connection || "";
}

function categoryLabel(category) {
  switch (category) {
    case "YEAR":     return "Same Year As...";
    case "GENRE":    return "Same Genre As...";
    case "ACTOR":    return "Actor Also In...";
    case "DIRECTOR": return "Also Directed By...";
    default:         return category;
  }
}

async function tmdbFetch(path, params = {}) {
  // TMDB discover uses dot notation for comparisons (e.g. vote_count.gte)
  if ("vote_count_gte" in params) { params["vote_count.gte"] = params.vote_count_gte; delete params.vote_count_gte; }
  if ("popularity_gte"  in params) { params["popularity.gte"]  = params.popularity_gte;  delete params.popularity_gte; }
  params.api_key = TMDB_KEY;
  const res = await fetch(`https://api.themoviedb.org/3${path}?${new URLSearchParams(params)}`);
  return res.json();
}

function showNoPuzzle() {
  clueGrid.innerHTML = `
    <div id="no-puzzle">
      <h2>No puzzle today 🎬</h2>
      <p>Check back tomorrow!</p>
    </div>`;
  document.getElementById("guess-section").style.display = "none";
}
