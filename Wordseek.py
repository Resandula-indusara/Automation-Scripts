#!/usr/bin/env python3
"""
WordSeek Bot v2  -  adaptive solver + screen-reading player
===========================================================

Usage
-----
  python wordseek_bot.py --calibrate                  # one-time setup (chat region + tile colours)
  python wordseek_bot.py --test-vision --length 5     # check the bot can read the board
  python wordseek_bot.py --simulate 200 --length 5    # benchmark the solver offline (no screen needed)
  python wordseek_bot.py --length 5                   # play one game
  python wordseek_bot.py --length 5 --games 10 --start-cmd "/new"

Install:  pip install pyautogui pillow numpy
Optional: pip install wordfreq pyperclip
          (wordfreq = prefer common words, pyperclip = paste instead of typing)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path

CONFIG_FILE = Path("wordseek_config.json")
STATS_FILE = Path("wordseek_stats.json")

# Opening words (first one that exists in your words.txt is used).
STARTERS = {
    4: ["tear", "rate", "tale", "sale", "lore", "oats"],
    5: ["crane", "slate", "stare", "raise", "arise", "soare"],
    6: ["retina", "tinsel", "stolen", "orange", "sailor", "master"],
}

ENTROPY_LIMIT = 300   # full information-gain search when <= this many candidates remain
PROBE_LIMIT = 60      # ...and also try non-candidate "probe" words when <= this many remain
PROBE_SAMPLE = 400    # how many probe words to try
MIN_TILE_PX = 6       # smallest width (px) a coloured run must have to count as a tile
MIN_BAND_H = 6        # smallest height (px) a row of tiles must have

EMOJI = {"G": "🟩", "Y": "🟨", "X": "🟥"}
LABEL_TO_CHAR = {1: "G", 2: "Y", 3: "X"}


# --------------------------------------------------------------------------- #
#  Dictionary + scoring
# --------------------------------------------------------------------------- #
def load_dictionary(length: int, path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        print(f"❌ '{path}' not found. Put one word per line in it "
              f"(a big English word list works best).")
        sys.exit(1)
    words = set()
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            w = line.strip().lower()
            if len(w) == length and w.isascii() and w.isalpha():
                words.add(w)
    if not words:
        print(f"❌ No {length}-letter words found in '{path}'.")
        sys.exit(1)
    return sorted(words)


def build_weights(words: list[str], use_freq: bool = True) -> dict[str, float]:
    """Common words get a higher prior if `wordfreq` is installed."""
    if use_freq:
        try:
            from wordfreq import zipf_frequency
            print("📈 Using word-frequency prior (wordfreq).")
            return {w: 2.0 ** zipf_frequency(w, "en") for w in words}
        except ImportError:
            print("ℹ️  Tip: 'pip install wordfreq' makes guesses favour common words.")
    return {w: 1.0 for w in words}


@lru_cache(maxsize=200_000)
def feedback_for(guess: str, answer: str) -> str:
    """Exact Wordle-style feedback (handles duplicate letters correctly).
    G = right spot, Y = wrong spot, X = absent."""
    n = len(guess)
    res = ["X"] * n
    spare: dict[str, int] = {}
    for i in range(n):
        if guess[i] == answer[i]:
            res[i] = "G"
        else:
            spare[answer[i]] = spare.get(answer[i], 0) + 1
    for i in range(n):
        if res[i] == "X":
            c = guess[i]
            if spare.get(c, 0):
                res[i] = "Y"
                spare[c] -= 1
    return "".join(res)


# --------------------------------------------------------------------------- #
#  Solver
# --------------------------------------------------------------------------- #
class Solver:
    def __init__(self, words, weights, length, banned=None):
        self.length = length
        self.weights = weights
        self.banned = banned if banned is not None else set()
        self.all_words = [w for w in words if w not in self.banned]
        self.candidates = list(self.all_words)
        self.history: list[tuple[str, str]] = []

    # -- state updates ------------------------------------------------------
    def update(self, guess: str, fb: str) -> None:
        self.history.append((guess, fb))
        self.candidates = [w for w in self.candidates if feedback_for(guess, w) == fb]

    def reject(self, word: str) -> None:
        """The game refused this word (not in its dictionary)."""
        self.banned.add(word)
        self.all_words = [w for w in self.all_words if w != word]
        self.candidates = [w for w in self.candidates if w != word]

    # -- choosing the next guess -------------------------------------------
    def next_guess(self) -> str:
        cands = self.candidates
        if not cands:
            raise RuntimeError("no candidates left")

        if not self.history:                                  # opening move
            present = set(self.all_words)
            for s in STARTERS.get(self.length, []):
                if s in present:
                    return s
            return self._frequency_pick(cands)

        if len(cands) <= 2:
            return max(cands, key=lambda w: self.weights[w])
        if len(cands) > ENTROPY_LIMIT:
            return self._frequency_pick(cands)

        pool = list(cands)
        if len(cands) <= PROBE_LIMIT:                         # allow splitter words
            cset = set(cands)
            extra = [w for w in self.all_words if w not in cset]
            if len(extra) > PROBE_SAMPLE:
                extra = random.sample(extra, PROBE_SAMPLE)
            pool += extra
        return self._entropy_pick(pool, cands)

    def _frequency_pick(self, cands: list[str]) -> str:
        """Fast heuristic for big candidate lists: positional + letter frequency."""
        from collections import Counter
        pos = [Counter() for _ in range(self.length)]
        letters: Counter = Counter()
        for w in cands:
            for i, ch in enumerate(w):
                pos[i][ch] += 1
            for ch in set(w):
                letters[ch] += 1

        def score(w: str):
            s = sum(pos[i][ch] for i, ch in enumerate(w))
            s += sum(letters[ch] for ch in set(w))
            return (s, self.weights[w])

        return max(cands, key=score)

    def _entropy_pick(self, pool: list[str], cands: list[str]) -> str:
        """Pick the guess that is expected to split the candidates best."""
        wt = self.weights
        total = sum(wt[c] for c in cands)
        cset = set(cands)
        best, best_score = pool[0], -1.0
        for g in pool:
            buckets: dict[str, float] = defaultdict(float)
            for c in cands:
                buckets[feedback_for(g, c)] += wt[c]
            ent = 0.0
            for mass in buckets.values():
                p = mass / total
                ent -= p * math.log2(p)
            if g in cset:
                ent += wt[g] / total          # bonus: it might simply be the answer
            if ent > best_score:
                best, best_score = g, ent
        return best


# --------------------------------------------------------------------------- #
#  Offline benchmark
# --------------------------------------------------------------------------- #
def simulate(words, weights, length, games, max_attempts) -> None:
    print(f"🧪 Simulating {games} games...")
    results = []
    t0 = time.time()
    for k in range(games):
        answer = random.choice(words)
        solver = Solver(words, weights, length)
        solved = None
        for attempt in range(1, max_attempts + 1):
            g = solver.next_guess()
            fb = feedback_for(g, answer)
            if fb == "G" * length:
                solved = attempt
                break
            solver.update(g, fb)
        results.append(solved)
        if (k + 1) % 25 == 0:
            print(f"   {k + 1}/{games} done")
    won = [r for r in results if r]
    print(f"\n✅ Solved {len(won)}/{games}   average guesses: {sum(won) / max(len(won), 1):.2f}"
          f"   time: {time.time() - t0:.1f}s")
    dist = defaultdict(int)
    for r in won:
        dist[r] += 1
    for a in sorted(dist):
        print(f"   {a:>2} guesses | {'█' * max(1, round(40 * dist[a] / games))} {dist[a]}")


# --------------------------------------------------------------------------- #
#  Screen reading
# --------------------------------------------------------------------------- #
def grab_screen(cfg):
    import pyautogui
    return pyautogui.screenshot(region=tuple(cfg["region"])).convert("RGB")


def labels_from_image(img, cfg):
    """0 = background, 1 = green tile, 2 = yellow tile, 3 = absent tile."""
    import numpy as np
    arr = np.asarray(img, dtype=np.float32)
    refs = [np.array(cfg["colors"][k], dtype=np.float32) for k in ("green", "yellow", "absent")]
    dist = np.stack([np.sqrt(((arr - r) ** 2).sum(axis=2)) for r in refs])
    near = dist.argmin(axis=0)
    ok = dist.min(axis=0) <= cfg.get("tolerance", 40)
    return np.where(ok, near + 1, 0).astype(np.uint8)


def _bands(labels, n):
    """Vertical bands (y0, y1) that look like one row of tiles."""
    import numpy as np
    row_px = (labels > 0).sum(axis=1)
    ys = np.flatnonzero(row_px >= max(MIN_TILE_PX * n, 16))
    if ys.size == 0:
        return []
    groups = np.split(ys, np.flatnonzero(np.diff(ys) > 2) + 1)
    return [(int(g[0]), int(g[-1])) for g in groups if g[-1] - g[0] + 1 >= MIN_BAND_H]


def _runs(col):
    runs, start = [], 0
    for x in range(1, len(col) + 1):
        if x == len(col) or col[x] != col[start]:
            if col[start] != 0 and x - start >= MIN_TILE_PX:
                runs.append((int(col[start]), start, x))
            start = x
    return runs


def _band_pattern(col, n):
    import numpy as np
    runs = _runs(col)
    if not runs:
        return None
    if len(runs) == n:                                  # tiles separated by gaps
        widths = [e - s for _, s, e in runs]
        if max(widths) <= 2 * min(widths):
            return "".join(LABEL_TO_CHAR[lab] for lab, _, _ in runs)
    start, end = runs[0][1], runs[-1][2]                # fallback: split span evenly
    if end - start < n * MIN_TILE_PX:
        return None
    cell = (end - start) / n
    out = []
    for i in range(n):
        a = int(start + i * cell + cell * 0.25)
        b = int(start + (i + 1) * cell - cell * 0.25)
        seg = col[a:max(b, a + 1)]
        seg = seg[seg > 0]
        if seg.size == 0:
            return None
        out.append(LABEL_TO_CHAR[int(np.bincount(seg, minlength=4).argmax())])
    return "".join(out)


def rows_from_image(img, cfg, n) -> list[str]:
    """All tile rows visible in the image, top to bottom, e.g. ['XYXGX', 'GGXYX']."""
    import numpy as np
    labels = labels_from_image(img, cfg)
    rows = []
    for y0, y1 in _bands(labels, n):
        sub = labels[y0:y1 + 1]
        h = sub.shape[0]
        counts = np.stack([(sub == k).sum(axis=0) for k in (1, 2, 3)])
        col = counts.argmax(axis=0) + 1
        col[counts.max(axis=0) < max(2, 0.25 * h)] = 0   # ignore glyph/noise columns
        pat = _band_pattern(col, n)
        if pat:
            rows.append(pat)
    return rows


def read_rows(cfg, n) -> list[str]:
    return rows_from_image(grab_screen(cfg), cfg, n)


def wait_for_new_row(cfg, n, before, timeout):
    """Wait until the visible rows differ from `before` AND stop changing.
    Returns the newest row, or None on timeout (= word probably rejected)."""
    deadline = time.time() + timeout
    last = None
    time.sleep(0.8)
    while time.time() < deadline:
        rows = read_rows(cfg, n)
        if rows and rows != before:
            if rows == last:
                return rows[-1]
            last = rows
        else:
            last = None
        time.sleep(0.4)
    return None


# --------------------------------------------------------------------------- #
#  Calibration / config
# --------------------------------------------------------------------------- #
def _countdown(msg, secs=4):
    import pyautogui
    print(f"\n👉 {msg}")
    for s in range(secs, 0, -1):
        print(f"   capturing in {s}...", end="\r", flush=True)
        time.sleep(1)
    pos = pyautogui.position()
    print(f"   captured at ({pos.x}, {pos.y})        ")
    return pos.x, pos.y


def calibrate() -> None:
    import pyautogui
    print("🎯 Calibration - have a game open that already shows one green, one yellow\n"
          "   and one absent (red/gray) tile. Hover the mouse as asked each time.")
    x1, y1 = _countdown("Hover over the TOP-LEFT corner of the chat area where tiles appear")
    x2, y2 = _countdown("Hover over the BOTTOM-RIGHT corner of that area")
    left, top = min(x1, x2), min(y1, y2)
    width, height = abs(x2 - x1), abs(y2 - y1)
    colors = {}
    for key, label in (("green", "a GREEN tile"),
                       ("yellow", "a YELLOW tile"),
                       ("absent", "an ABSENT (red/gray) tile")):
        x, y = _countdown(f"Hover over the centre of {label}")
        r, g, b = pyautogui.pixel(x, y)[:3]
        colors[key] = [int(r), int(g), int(b)]
        print(f"   {key}: RGB {colors[key]}")
    cfg = {"region": [left, top, width, height], "colors": colors, "tolerance": 40}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"\n💾 Saved to {CONFIG_FILE}. Next: python wordseek_bot.py --test-vision --length 5")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print("❌ No calibration found. Run:  python wordseek_bot.py --calibrate")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def test_vision(cfg, n) -> None:
    img = grab_screen(cfg)
    img.save("debug_capture.png")
    rows = rows_from_image(img, cfg, n)
    print("📸 Saved debug_capture.png (exactly what the bot sees).")
    if not rows:
        print("⚠️  No tile rows found. Re-run --calibrate, or raise 'tolerance' in the config.")
        return
    print(f"✅ Found {len(rows)} row(s):")
    for r in rows:
        print("   ", "".join(EMOJI[c] for c in r), r)


# --------------------------------------------------------------------------- #
#  Playing
# --------------------------------------------------------------------------- #
def type_text(text: str, paste: bool = False) -> None:
    import pyautogui
    if paste:
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
    else:
        pyautogui.write(text, interval=0.02)
    pyautogui.press("enter")


def play_game(cfg, solver: Solver, args) -> dict:
    n = solver.length
    attempts, reject_streak, streak_words = 0, 0, []
    t0 = time.time()

    while attempts < args.max_attempts:
        guess = solver.next_guess()
        before = read_rows(cfg, n)
        type_text(guess, args.paste)
        row = wait_for_new_row(cfg, n, before, args.timeout)

        if row is None:
            reject_streak += 1
            streak_words.append(guess)
            print(f"⚠️  No new row after '{guess}' - assuming the game rejected it, trying another.")
            solver.reject(guess)
            if reject_streak >= 5:
                solver.banned.difference_update(streak_words)   # probably not real rejections
                print("🛑 5 guesses in a row got no response. Check --test-vision, the focus on "
                      "the chat box, your keyboard layout (must be English), or raise --timeout.")
                return {"won": False, "attempts": attempts, "answer": None,
                        "seconds": round(time.time() - t0, 1)}
            continue

        reject_streak, streak_words = 0, []
        attempts += 1
        left = len(solver.candidates)
        print(f"#{attempts:<2} {guess.upper():<8} {''.join(EMOJI[c] for c in row)}"
              f"   ({left:,} candidates before this guess)")

        if row == "G" * n:
            print(f"🎉 Solved in {attempts} guesses!  Answer: {guess.upper()}")
            return {"won": True, "attempts": attempts, "answer": guess,
                    "seconds": round(time.time() - t0, 1)}

        solver.update(guess, row)
        if not solver.candidates:
            print("❌ No word in the dictionary fits all the feedback. Either a colour was "
                  "misread (run --test-vision) or the answer is missing from words.txt.")
            return {"won": False, "attempts": attempts, "answer": None,
                    "seconds": round(time.time() - t0, 1)}
        if len(solver.candidates) <= 8:
            print(f"    🎯 left: {', '.join(solver.candidates)}")

    print("🏁 Max attempts reached.")
    return {"won": False, "attempts": attempts, "answer": None,
            "seconds": round(time.time() - t0, 1)}


def save_stats(entry: dict) -> None:
    data = []
    if STATS_FILE.exists():
        try:
            data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
    data.append(entry)
    STATS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(description="WordSeek Bot v2")
    p.add_argument("--length", type=int, help="word length (4 / 5 / 6)")
    p.add_argument("--words", default="words.txt", help="dictionary file (default words.txt)")
    p.add_argument("--max-attempts", type=int, default=30)
    p.add_argument("--timeout", type=float, default=10.0,
                   help="seconds to wait for the game's reply before treating a word as rejected")
    p.add_argument("--games", type=int, default=1, help="games to play in a row (needs --start-cmd)")
    p.add_argument("--start-cmd", default="", help='text that starts a game, e.g. "/new"')
    p.add_argument("--start-delay", type=float, default=3.0)
    p.add_argument("--paste", action="store_true", help="paste words (pyperclip) instead of typing")
    p.add_argument("--no-freq", action="store_true", help="ignore word frequencies")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--test-vision", action="store_true")
    p.add_argument("--simulate", type=int, metavar="N", help="benchmark the solver on N random words")
    return p.parse_args()


def ask_length() -> int:
    while True:
        raw = input("🔢 Word length (4 / 5 / 6): ").strip()
        if raw.isdigit() and 3 <= int(raw) <= 10:
            return int(raw)
        print("❌ Please enter a number like 4, 5 or 6.")


def main() -> None:
    args = parse_args()

    if args.calibrate:
        calibrate()
        return

    length = args.length or ask_length()
    words = load_dictionary(length, args.words)
    print(f"📄 Loaded {len(words):,} words of length {length}.")
    weights = build_weights(words, use_freq=not args.no_freq)

    if args.simulate:
        simulate(words, weights, length, args.simulate, args.max_attempts)
        return

    cfg = load_config()
    if args.test_vision:
        test_vision(cfg, length)
        return

    if args.games > 1 and not args.start_cmd:
        print("ℹ️  --games > 1 needs --start-cmd (e.g. \"/new\"). Playing a single game.")
        args.games = 1

    import pyautogui
    pyautogui.FAILSAFE = True      # slam the mouse into a screen corner to abort
    pyautogui.PAUSE = 0.02

    print("\n🚀 Starting in 5 seconds - click the game's message box now! "
          "(Move the mouse to a screen corner to stop.)")
    time.sleep(5)

    banned: set[str] = set()
    session = []
    for g in range(1, args.games + 1):
        print(f"\n=========== Game {g}/{args.games} ===========")
        if args.start_cmd:
            type_text(args.start_cmd, args.paste)
            time.sleep(args.start_delay)
        solver = Solver(words, weights, length, banned)
        result = play_game(cfg, solver, args)
        result.update({"time": datetime.now().isoformat(timespec="seconds"), "length": length})
        session.append(result)
        save_stats(result)

    wins = [r for r in session if r["won"]]
    print(f"\n📊 Session: {len(wins)}/{len(session)} won"
          + (f", average {sum(r['attempts'] for r in wins) / len(wins):.2f} guesses" if wins else "")
          + f"  (history in {STATS_FILE})")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "FailSafeException":
            print("\n🛑 Mouse moved to a screen corner - bot stopped.")
        else:
            raise