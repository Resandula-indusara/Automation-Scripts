"""
Human-like typing simulator.

1. Put your text in `assignment.txt` (UTF-8) next to this script.
2. Run:  python type.py            (or: python type.py other_file.txt)
3. Click into the document during the 8-second countdown.

Abort at any time: slam the mouse into a screen corner, or press Ctrl+C.
"""

import random
import sys
import time
from pathlib import Path

import pyautogui

try:
    import pyperclip  # pip install pyperclip  (needed for ’ “ ” — and other non-ASCII characters)
except ImportError:
    pyperclip = None

# pyautogui waits 0.1s after EVERY call by default, which silently wrecks the timing below.
pyautogui.PAUSE = 0
# Safety: slam the mouse into a screen corner to abort the script instantly.
pyautogui.FAILSAFE = True

# Make emoji / Sinhala prints work on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
TEXT_FILE = "assignment.txt"

# Raw typing speed (words per minute, 1 word = 5 characters) between pauses.
# The speed drifts randomly inside this range while typing; punctuation pauses,
# typos and breaks pull the *effective* speed lower.
WPM_RANGE = (55, 85)

TYPO_CHANCE = 0.02          # chance of a typo on any letter/digit
DOUBLE_TYPO_CHANCE = 0.12   # chance (10-15%) that fixing a typo goes wrong too

BREAK_EVERY_WORDS = (40, 50)   # take a rest after this many words (random in range)
BREAK_SECONDS = (3.0, 7.0)     # how long each rest lasts

PASTE_KEY = "command" if sys.platform == "darwin" else "ctrl"
SCRIPT_DIR = Path(__file__).resolve().parent
STATUS_WIDTH = 60
PUNCTUATION = ".,?!:;"

# Keys physically next to each key on a QWERTY keyboard (used for realistic typos)
NEIGHBORS = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh",
    "u": "yij", "i": "uok", "o": "ipl", "p": "ol",
    "a": "qsz", "s": "awdx", "d": "sefc", "f": "drgv", "g": "fthb",
    "h": "gyjn", "j": "hukm", "k": "jil", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
    "1": "2", "2": "13", "3": "24", "4": "35", "5": "46",
    "6": "57", "7": "68", "8": "79", "9": "80", "0": "9",
}

_warned_unicode = False


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def load_text(filename):
    """Read the text to type from a UTF-8 file. Returns None (after explaining why) on failure."""
    given = Path(filename)
    candidates = [given] if given.is_absolute() else [Path.cwd() / given, SCRIPT_DIR / given]

    for path in candidates:
        try:
            # utf-8-sig = UTF-8 that also silently drops the BOM Windows Notepad can add
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except FileNotFoundError:
            continue  # try the next location
        except UnicodeDecodeError:
            print(f"❌ '{path}' is not valid UTF-8. Re-save it as UTF-8 "
                  "(Notepad: File → Save As → Encoding: UTF-8) and try again.")
            return None
        except OSError as err:
            print(f"❌ Could not read '{path}': {err}")
            return None

        text = text.rstrip()  # a trailing newline would press Enter at the very end
        if not text:
            print(f"❌ '{path}' is empty. Paste your text into it and save the file.")
            return None
        print(f"📄 Loaded {len(text):,} characters from {path}")
        return text

    print(f"❌ Could not find '{filename}'.")
    print(f"   Create it next to the script ({SCRIPT_DIR}) or in your current folder,")
    print("   paste your text into it, and save it as UTF-8.")
    return None


def safe_startup_delay():
    """8-second countdown so you can click into the Word document."""
    print("🚀 The script has started! Quickly click on the required spot....")
    for i in range(8, 0, -1):
        print(f"⏳ You have {i} seconds left. ...")
        time.sleep(1)
    print("▶️ Start typing")


def wait(seconds):
    """Sleep in short slices and re-check the fail-safe, so a corner-abort
    works instantly even in the middle of a long pause or break."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))
        pyautogui.failSafeCheck()


def show_status(message):
    """Rewrite the single status line in the terminal without cluttering the console."""
    sys.stdout.write("\r" + message.ljust(STATUS_WIDTH))
    sys.stdout.flush()


def progress_line(percent):
    return f"⚡ Typing progress: {percent}%"


def neighbor_key(char):
    """A plausible wrong key for `char` (a key next to it), preserving case."""
    wrong = random.choice(NEIGHBORS[char.lower()])
    return wrong.upper() if char.isupper() else wrong


def type_char(char):
    """Type one character. pyautogui can't type non-ASCII (’ “ ” — é ...), so paste those."""
    global _warned_unicode
    if char.isascii():
        pyautogui.write(char)
    elif pyperclip:
        pyperclip.copy(char)
        pyautogui.hotkey(PASTE_KEY, "v")
        time.sleep(0.05)
    else:
        if not _warned_unicode:
            print(f"\n⚠️ Skipping non-ASCII character {char!r} (and any others). "
                  "Run `pip install pyperclip` to type them.")
            _warned_unicode = True


# ----------------------------------------------------------------------------
# The simulator
# ----------------------------------------------------------------------------
class HumanTypist:
    def __init__(self):
        self.wpm = random.uniform(*WPM_RANGE)
        self.percent = -1

    # --- pacing -------------------------------------------------------------
    def _drift_speed(self):
        """Nudge the typing speed a little (called once per word)."""
        self.wpm += random.uniform(-4, 4)
        self.wpm = max(WPM_RANGE[0], min(WPM_RANGE[1], self.wpm))

    def _key_delay(self, factor=1.0):
        """Delay after one keystroke. 60s / (WPM * 5 chars) with natural jitter."""
        return (12.0 / self.wpm) * random.uniform(0.6, 1.4) * factor

    def _pause_scale(self):
        """Slower typing => proportionally longer thinking pauses."""
        return (sum(WPM_RANGE) / 2) / self.wpm

    # --- typos --------------------------------------------------------------
    def _make_typo(self, char):
        """Hit a wrong key, notice, and fix it. Sometimes the fix goes wrong too."""
        pyautogui.write(neighbor_key(char))
        wait(random.uniform(0.18, 0.35))  # "noticing" the mistake
        backspaces = 1

        if random.random() < DOUBLE_TYPO_CHANCE:
            if random.random() < 0.5:
                # Still aiming at the wrong spot: a second wrong key lands before the fix
                pyautogui.write(neighbor_key(char))
                backspaces = 2
            else:
                # Starts correcting, but fumbles the retry with another wrong key
                pyautogui.press("backspace")
                wait(random.uniform(0.12, 0.25))
                pyautogui.write(neighbor_key(char))
            wait(random.uniform(0.35, 0.7))  # frustrated beat

        for _ in range(backspaces):
            pyautogui.press("backspace")
            wait(random.uniform(0.12, 0.25))

    # --- one character ------------------------------------------------------
    def _type_one(self, char, next_char):
        # Subtle deceleration on the letter just before a punctuation mark
        approach = 1.25 if next_char in PUNCTUATION else 1.0

        # 1. Spaces and new lines
        if char == " ":
            pyautogui.press("space")
            wait(self._key_delay(1.15))

        elif char == "\n":
            pyautogui.press("enter")
            wait(random.uniform(1.2, 2.5))  # longer pause between paragraphs

        # 2. ASCII letters and digits, with typo simulation
        elif char.isascii() and char.lower() in NEIGHBORS:
            if random.random() < TYPO_CHANCE:
                self._make_typo(char)

            pyautogui.write(char)
            wait(self._key_delay(approach))

        # 3. Punctuation / symbols / non-ASCII
        else:
            type_char(char)
            scale = self._pause_scale()
            # Only pause at the END of a sentence ("3.1" or "e.g." shouldn't trigger it)
            if char in ".?!" and next_char in " \n":
                wait(random.uniform(0.8, 1.6) * scale)
            elif char in ",:;":
                wait(random.uniform(0.3, 0.7) * scale)
            else:
                wait(self._key_delay())

    # --- progress & breaks --------------------------------------------------
    def _report(self, percent):
        if percent != self.percent:
            self.percent = percent
            show_status(progress_line(percent))

    def _take_break(self):
        """Look away from the screen for a few seconds, then come back at a fresh pace."""
        seconds = random.uniform(*BREAK_SECONDS)
        show_status(f"☕ Taking a short break ({seconds:.1f}s)...")
        wait(seconds)
        self.wpm = random.uniform(*WPM_RANGE)
        show_status(progress_line(self.percent))

    # --- main loop ----------------------------------------------------------
    def type_text(self, text):
        total = len(text)
        in_word = False
        words_since_break = 0
        next_break = random.randint(*BREAK_EVERY_WORDS)

        self._report(0)
        for i, char in enumerate(text):
            next_char = text[i + 1] if i + 1 < total else "\n"

            if char != "\r":
                self._type_one(char, next_char)

            # Count finished words
            break_due = False
            if char.isspace():
                if in_word:
                    in_word = False
                    words_since_break += 1
                    self._drift_speed()
                    break_due = words_since_break >= next_break and i + 1 < total
            else:
                in_word = True

            self._report((i + 1) * 100 // total)

            if break_due:
                self._take_break()
                words_since_break = 0
                next_break = random.randint(*BREAK_EVERY_WORDS)


def main():
    text_file = sys.argv[1] if len(sys.argv) > 1 else TEXT_FILE
    text = load_text(text_file)
    if text is None:
        sys.exit(1)

    # Pasting non-ASCII characters borrows the clipboard; remember what was on it.
    saved_clipboard = None
    if pyperclip and not text.isascii():
        try:
            saved_clipboard = pyperclip.paste()
        except Exception:
            saved_clipboard = None

    try:
        safe_startup_delay()
        HumanTypist().type_text(text)
    except pyautogui.FailSafeException:
        print("\n🛑 Aborted (mouse hit a screen corner).")
        return
    except KeyboardInterrupt:
        print("\n🛑 Stopped with Ctrl+C.")
        return
    finally:
        if saved_clipboard is not None:
            try:
                pyperclip.copy(saved_clipboard)
            except Exception:
                pass

    print("\n✅ Finished!")


if __name__ == "__main__":
    main()