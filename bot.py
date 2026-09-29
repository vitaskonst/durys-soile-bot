import os
import asyncio
import logging
import tempfile
import wave
from dataclasses import dataclass
import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile

load_dotenv()
# aiogram reports polling problems (a revoked token, a second instance with
# the same token) only through logging, so without this they are invisible.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s"
)
TOKEN = os.environ["TOKEN"]
# The backend's base URL, e.g. https://example.org/api/v1.0
API_BASE_URL = os.environ["API_BASE_URL"].rstrip("/")

word_limit = 10


@dataclass
class ListState:
    offset: int = 0  # page number, as the API's offset parameter expects
    filter: str = ""


# The page and search filter of each chat's word lists, one per list type, so
# users paging at the same time do not move each other's lists. Kept in memory
# only: after a restart, the page buttons of an existing list start again
# from its first page, unfiltered.
list_states: dict[tuple[int, str], ListState] = {}


def list_state(chat_id: int, word_type: str) -> ListState:
    return list_states.setdefault((chat_id, word_type), ListState())

# The last voice message sent to each chat. It is deleted before the next one
# is sent, so Telegram's player never queues earlier clips after the new one.
# Kept in memory only: after a restart, the first clip in a chat leaves the
# previous one in place.
last_voice: dict[int, int] = {}

# Initialize bot, storage, dispatcher, and router
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# COMMAND HANDLERS
@router.message(Command("start"))
async def start(message: Message):
    await message.answer(f"Сәлем, {message.from_user.first_name}!")

@router.message(Command("parasite_words"))
async def list_parasite_words(message: Message):
    state = list_states[(message.chat.id, "parasite")] = ListState(
        filter=" ".join(message.text.split()[1:])
    )
    parasite_markup = await parasite_words_markup(state.offset, state.filter)
    if parasite_markup:
        await message.answer("Бөгде тіл сөздер:", reply_markup=parasite_markup)

async def parasite_words_markup(offset, filter_text):
    url = f"{API_BASE_URL}/words?type=parasite&offset={offset}&limit={word_limit}&sort=asc"
    if filter_text:
        url += f"&filter={filter_text}"

    parasite_words = requests.get(url)

    if parasite_words.status_code != 200:
        return None

    data = parasite_words.json()
    if not data:
        return None

    buttons = []
    for word in data:
        buttons.append([InlineKeyboardButton(text=word["word"], callback_data=str(word["id"]))])

    if offset > 0 and len(data) == word_limit:
        buttons.append([InlineKeyboardButton(text="⏪ Артқа", callback_data="parasite_prev_page"),
                        InlineKeyboardButton(text="Келесі ⏩", callback_data="parasite_next_page")])
    elif offset > 0:
        buttons.append([InlineKeyboardButton(text="⏪ Артқа", callback_data="parasite_prev_page")])
    elif len(data) == word_limit:
         buttons.append([InlineKeyboardButton(text="Келесі ⏩", callback_data="parasite_next_page")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    return markup

@router.message(Command("mispronounced_words"))
async def list_mispronounced_words(message: Message):
    state = list_states[(message.chat.id, "mispronounced")] = ListState(
        filter=" ".join(message.text.split()[1:])
    )
    mispro_markup = await mispronounced_words_markup(state.offset, state.filter)
    if mispro_markup:
        await message.answer("Жиі қате айтылатын сөздер:", reply_markup=mispro_markup)

async def mispronounced_words_markup(offset, filter_text):
    url = f"{API_BASE_URL}/words?type=commonly-mispronounced&offset={offset}&limit={word_limit}&sort=asc"
    if filter_text:
        url += f"&filter={filter_text}"

    mispronounced_words = requests.get(url)

    if mispronounced_words.status_code != 200:
        return None

    data = mispronounced_words.json()
    if not data:
        return None
    
    buttons = []
    for word in data:
        buttons.append([InlineKeyboardButton(text=word["word"], callback_data=str(word["id"]))])

    if offset > 0 and len(data) == word_limit:
        buttons.append([InlineKeyboardButton(text="⏪ Артқа", callback_data="mispro_prev_page"),
                        InlineKeyboardButton(text="Келесі ⏩", callback_data="mispro_next_page")])
    elif offset > 0:
        buttons.append([InlineKeyboardButton(text="⏪ Артқа", callback_data="mispro_prev_page")])
    elif len(data) == word_limit:
         buttons.append([InlineKeyboardButton(text="Келесі ⏩", callback_data="mispro_next_page")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    return markup


# DEFAULT MESSAGE HANDLER
@router.message()
async def default_handler(message: Message):
    await message.answer("Сізді түсінбедім(")


# CALLBACK QUERY HANDLERS
async def edit_list(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        # The page did not change, e.g. a list opened before a restart, whose
        # page the bot no longer remembers. Telegram rejects no-op edits.
        if "message is not modified" not in str(error):
            raise


@router.callback_query(F.data == "parasite_prev_page")
async def parasite_prev_page(callback: CallbackQuery):
    await callback.answer()
    state = list_state(callback.message.chat.id, "parasite")
    state.offset = max(0, state.offset - 1)
    markup = await parasite_words_markup(state.offset, state.filter)
    await edit_list(callback, "Бөгде тіл сөздер:", markup)

@router.callback_query(F.data == "parasite_next_page")
async def parasite_next_page(callback: CallbackQuery):
    await callback.answer()
    state = list_state(callback.message.chat.id, "parasite")
    state.offset += 1
    markup = await parasite_words_markup(state.offset, state.filter)
    await edit_list(callback, "Бөгде тіл сөздер:", markup)

@router.callback_query(F.data == "mispro_prev_page")
async def mispro_prev_page(callback: CallbackQuery):
    await callback.answer()
    state = list_state(callback.message.chat.id, "mispronounced")
    state.offset = max(0, state.offset - 1)
    markup = await mispronounced_words_markup(state.offset, state.filter)
    await edit_list(callback, "Жиі қате айтылатын сөздер:", markup)

@router.callback_query(F.data == "mispro_next_page")
async def mispro_next_page(callback: CallbackQuery):
    await callback.answer()
    state = list_state(callback.message.chat.id, "mispronounced")
    state.offset += 1
    markup = await mispronounced_words_markup(state.offset, state.filter)
    await edit_list(callback, "Жиі қате айтылатын сөздер:", markup)

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

    word_data = word.json()
    # Only parasite words carry correctVersions; the API omits the key for
    # commonly mispronounced ones. Usage examples are omitted when absent.
    if word_data.get("correctVersions"):
        correct_version = word_data["correctVersions"][0]
        audio_text = f"❌ {word_data['word']}\n✅ {correct_version['word']}"
        if correct_version.get("incorrectUsage") or correct_version.get("correctUsage"):
            audio_text += "\n"
            if correct_version.get("incorrectUsage"):
                audio_text += f"\n❌ {correct_version['incorrectUsage']}"
            if correct_version.get("correctUsage"):
                audio_text += f"\n✅ {correct_version['correctUsage']}"
    else:
        audio_text = f"🎧 {word_data['word']}"

    try:
        voice, duration = await to_voice(audio.content)
    except (RuntimeError, OSError, wave.Error):
        logging.exception("could not convert the clip of word %s", word_id)
        await callback.message.answer("Error!")
        return

    previous = last_voice.pop(chat_id, None)
    if previous:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=previous)
        except TelegramBadRequest:
            pass  # already deleted by the user, or too old to delete

    sent = await bot.send_voice(
        chat_id=chat_id,
        voice=BufferedInputFile(voice, filename=f"{word_id}.ogg"),
        caption=audio_text,
        duration=duration,
    )
    last_voice[chat_id] = sent.message_id


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("bot has been stopped from terminal")
