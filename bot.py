import os
import asyncio
import logging
import tempfile
import wave
import hashlib
import sqlite3
from collections import defaultdict
import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultAudio, Message, ReplyKeyboardRemove,
)

load_dotenv()
# aiogram reports polling problems (a revoked token, a second instance with
# the same token) only through logging, so without this they are invisible.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s"
)
TOKEN = os.environ["TOKEN"]
# The backend's base URL, e.g. https://example.org/api/v1.0
API_BASE_URL = os.environ["API_BASE_URL"].rstrip("/")
# SQLite file for the little state that has to survive restarts; see below.
STATE_DB = os.environ.get("STATE_DB", "state.sqlite3")

word_limit = 10

# The two word lists: API type and the heading shown above the list. The key
# is what page buttons carry in their callback data.
LISTS = {
    "p": ("parasite", "Бөгде тіл сөздер:"),
    "m": ("commonly-mispronounced", "Жиі қате айтылатын сөздер:"),
}

# Page buttons carry their list, page and search filter in their callback
# data, so a list keeps working across restarts and several lists in a chat
# never interfere. Telegram limits callback data to 64 bytes; a filter too long
# to fit is stored in the long_filters table instead, under a short key.
CALLBACK_DATA_LIMIT = 64

# State kept across restarts:
#   last_voice   -- the last voice message sent to each chat. It is deleted
#                   before the next one is sent, so Telegram's player never
#                   queues earlier clips after the new one.
#   long_filters -- search filters too long for a page button's data.
state = sqlite3.connect(STATE_DB, isolation_level=None)
state.execute("CREATE TABLE IF NOT EXISTS last_voice (chat_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL)")
state.execute("CREATE TABLE IF NOT EXISTS long_filters (key TEXT PRIMARY KEY, filter TEXT NOT NULL)")

# Serialises sending clips within a chat, so two quick taps cannot both see
# the same previous clip and leave one of the new ones behind.
voice_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# Initialize bot, dispatcher, and router
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


def page_data(kind: str, offset: int, filter_text: str) -> str:
    data = f"page:{kind}:{offset}:{filter_text}"
    if len(data.encode()) <= CALLBACK_DATA_LIMIT:
        return data
    key = hashlib.sha1(filter_text.encode()).hexdigest()[:12]
    state.execute("INSERT OR IGNORE INTO long_filters (key, filter) VALUES (?, ?)", (key, filter_text))
    return f"pageh:{kind}:{offset}:{key}"


def parse_page_data(data: str) -> tuple[str, int, str] | None:
    """(list, page, filter) from a page button, or None if it can't be served."""
    try:
        marker, kind, offset, filter_text = data.split(":", 3)
        if marker == "pageh":
            row = state.execute("SELECT filter FROM long_filters WHERE key = ?", (filter_text,)).fetchone()
            filter_text = row[0]
        if kind not in LISTS or int(offset) < 0:
            return None
        return kind, int(offset), filter_text
    except (ValueError, TypeError):  # malformed data, or an unknown filter key
        return None


class ApiUnavailable(Exception):
    pass


API_UNAVAILABLE = "Сөздерді алу мүмкін болмады. Кейінірек қайталап көріңіз."


def fetch_words(kind: str, offset: int, filter_text: str, limit: int = word_limit) -> list[dict]:
    """One page of a list (offset is a page number). Raises ApiUnavailable."""
    params = {"type": LISTS[kind][0], "offset": offset, "limit": limit, "sort": "asc"}
    if filter_text:
        params["filter"] = filter_text

    try:
        response = requests.get(f"{API_BASE_URL}/words", params=params, timeout=15)
    except requests.RequestException as error:
        raise ApiUnavailable(str(error)) from error
    if response.status_code != 200:
        raise ApiUnavailable(f"GET /words answered {response.status_code}")
    return response.json()


def word_caption(word: dict) -> str:
    # Only parasite words carry correctVersions; the API omits the key for
    # commonly mispronounced ones. Usage examples are omitted when absent.
    if not word.get("correctVersions"):
        return f"🎧 {word['word']}"
    correct_version = word["correctVersions"][0]
    caption = f"❌ {word['word']}\n✅ {correct_version['word']}"
    if correct_version.get("incorrectUsage") or correct_version.get("correctUsage"):
        caption += "\n"
        if correct_version.get("incorrectUsage"):
            caption += f"\n❌ {correct_version['incorrectUsage']}"
        if correct_version.get("correctUsage"):
            caption += f"\n✅ {correct_version['correctUsage']}"
    return caption


async def words_markup(kind: str, offset: int, filter_text: str):
    """The page's buttons, or None if the page has no words.

    Raises ApiUnavailable if the API cannot be reached or fails.
    """
    data = fetch_words(kind, offset, filter_text)
    if not data:
        return None

    buttons = []
    for word in data:
        buttons.append([InlineKeyboardButton(text=word["word"], callback_data=str(word["id"]))])

    previous = InlineKeyboardButton(text="⏪ Артқа", callback_data=page_data(kind, offset - 1, filter_text))
    following = InlineKeyboardButton(text="Келесі ⏩", callback_data=page_data(kind, offset + 1, filter_text))
    if offset > 0 and len(data) == word_limit:
        buttons.append([previous, following])
    elif offset > 0:
        buttons.append([previous])
    elif len(data) == word_limit:
        buttons.append([following])

    # Puts "@<bot> " into the input field, starting an inline search.
    buttons.append([InlineKeyboardButton(text="🔍 Іздеу", switch_inline_query_current_chat="")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# COMMAND HANDLERS
@router.message(Command("start"))
async def start(message: Message):
    # ReplyKeyboardRemove: clears the reply keyboard an earlier version of the
    # bot attached to the chat, which Telegram keeps showing until removed.
    await message.answer(f"Сәлем, {message.from_user.first_name}!", reply_markup=ReplyKeyboardRemove())


async def show_list(message: Message, kind: str):
    # Anything after the command is a search filter: the words' beginning.
    filter_text = " ".join(message.text.split()[1:])
    try:
        markup = await words_markup(kind, 0, filter_text)
    except ApiUnavailable:
        logging.exception("could not list words")
        await message.answer(API_UNAVAILABLE)
        return

    if markup is None:
        if filter_text:
            await message.answer(f"«{filter_text}» деп басталатын сөз табылмады.")
        else:
            await message.answer("Тізім бос.")
        return

    await message.answer(LISTS[kind][1], reply_markup=markup)


@router.message(Command("parasite_words"))
async def list_parasite_words(message: Message):
    await show_list(message, "p")


@router.message(Command("mispronounced_words"))
async def list_mispronounced_words(message: Message):
    await show_list(message, "m")


# DEFAULT MESSAGE HANDLER
@router.message()
async def default_handler(message: Message):
    # Also what pressing a leftover reply-keyboard button lands on, so remove
    # that keyboard here too.
    await message.answer("Сізді түсінбедім(", reply_markup=ReplyKeyboardRemove())


# CALLBACK QUERY HANDLERS
async def edit_list(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        # The page did not change, e.g. a button pressed twice in a row.
        # Telegram rejects no-op edits.
        if "message is not modified" not in str(error):
            raise


@router.callback_query(F.data.startswith("page:") | F.data.startswith("pageh:"))
async def turn_page(callback: CallbackQuery):
    page = parse_page_data(callback.data)
    if page is None:
        await expired_button(callback)
        return
    kind, offset, filter_text = page
    try:
        markup = await words_markup(kind, offset, filter_text)
    except ApiUnavailable:
        logging.exception("could not list words")
        await callback.answer(API_UNAVAILABLE, show_alert=True)
        return
    if markup is None:
        # Only reachable through an outdated list, e.g. after words were deleted.
        await callback.answer("Бұл бетте сөз қалмады.", show_alert=True)
        return

    await callback.answer()
    await edit_list(callback, LISTS[kind][1], markup)


async def run(*command: str) -> None:
    process = await asyncio.create_subprocess_exec(*command, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{command[0]} failed: {stderr.decode(errors='replace').strip()}")


async def to_voice(mp3: bytes) -> tuple[bytes, int]:
    """Re-encode an MP3 clip as a mono OGG/Opus voice note.

    Telegram draws a voice message's waveform only for OGG/Opus; an MP3 sent
    as a voice message shows a flat line. Returns (ogg bytes, duration in
    whole seconds).
    """
    with tempfile.TemporaryDirectory() as tmp:
        mp3_path, wav_path, ogg_path = (os.path.join(tmp, name) for name in ("in.mp3", "in.wav", "out.ogg"))
        with open(mp3_path, "wb") as f:
            f.write(mp3)
        await run("mpg123", "--quiet", "--mono", "-w", wav_path, mp3_path)
        # mpg123 exits 0 on input it cannot decode, just without writing.
        if not os.path.exists(wav_path):
            raise RuntimeError("mpg123 could not decode the clip")
        with wave.open(wav_path) as w:
            duration = max(1, round(w.getnframes() / w.getframerate()))
        await run("opusenc", "--quiet", "--bitrate", "48", wav_path, ogg_path)
        with open(ogg_path, "rb") as f:
            return f.read(), duration


@router.callback_query(F.data.isdigit())
async def send_word_audio(callback: CallbackQuery):
    # Acknowledge the press at once, or Telegram keeps the button spinning.
    await callback.answer()
    chat_id = callback.message.chat.id
    word_id = callback.data

    audio = requests.get(f"{API_BASE_URL}/audio/{word_id}")
    word = requests.get(f"{API_BASE_URL}/words/{word_id}")
    if audio.status_code != 200 or word.status_code != 200:
        await callback.message.answer("Error!")
        return

    audio_text = word_caption(word.json())

    try:
        voice, duration = await to_voice(audio.content)
    except (RuntimeError, OSError, wave.Error):
        logging.exception("could not convert the clip of word %s", word_id)
        await callback.message.answer("Error!")
        return

    async with voice_locks[chat_id]:
        row = state.execute("SELECT message_id FROM last_voice WHERE chat_id = ?", (chat_id,)).fetchone()
        if row:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=row[0])
            except TelegramBadRequest:
                pass  # already deleted by the user, or older than Telegram's 48 hours

        sent = await bot.send_voice(
            chat_id=chat_id,
            voice=BufferedInputFile(voice, filename=f"{word_id}.ogg"),
            caption=audio_text,
            duration=duration,
        )
        state.execute(
            "INSERT OR REPLACE INTO last_voice (chat_id, message_id) VALUES (?, ?)",
            (chat_id, sent.message_id),
        )


# INLINE MODE
INLINE_PAGE = 10  # words per list in one page of inline results


@router.inline_query()
async def inline_search(query: InlineQuery):
    """Search as you type: "@<bot> абай" in any chat lists matching words from
    both lists, and choosing one sends its clip as an audio message.

    Telegram fetches the clip itself from the API's MP3 URL, so API_BASE_URL
    must be reachable from the internet. Needs inline mode enabled for the bot
    in @BotFather (/setinline).
    """
    text = query.query.strip()
    if not text:
        await query.answer([], cache_time=300)
        return

    page = int(query.offset) if query.offset.isdigit() else 0
    results, more = [], False
    try:
        for kind in LISTS:
            words = fetch_words(kind, page, text, limit=INLINE_PAGE)
            more = more or len(words) == INLINE_PAGE
            for word in words:
                correct = word.get("correctVersions")
                results.append(InlineQueryResultAudio(
                    id=str(word["id"]),
                    audio_url=f"{API_BASE_URL}/audio/{word['id']}",
                    title=word["word"],
                    performer=f"✅ {correct[0]['word']}" if correct else "Дұрыс сөйле",
                    caption=word_caption(word),
                ))
    except ApiUnavailable:
        logging.exception("inline search failed")
        await query.answer([], cache_time=5)
        return

    await query.answer(results, cache_time=300, next_offset=str(page + 1) if more else "")


@router.callback_query()
async def expired_button(callback: CallbackQuery):
    # Buttons this version cannot serve, such as lists sent by an earlier
    # version of the bot. Registered last, so it only catches what no other
    # handler matched.
    await callback.answer("Бұл тізім ескірген. Команданы қайта жіберіңіз.", show_alert=True)


# The command menu Telegram shows, set on every start so it always matches the
# handlers of the running code.
COMMANDS = [
    BotCommand(command="start", description="Бастау"),
    BotCommand(command="parasite_words", description="Бөгде сөздер"),
    BotCommand(command="mispronounced_words", description="Жиі қате айтылатын сөздер"),
]


async def main():
    await bot.set_my_commands(COMMANDS)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("bot has been stopped from terminal")
