
import os
import re
import asyncio
import logging
import glob
import threading
from flask import Flask
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler

# Import translations
from translations import translations

# --- Setup ---
load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment & State ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- Multi-User Session Management ---
user_clients = {}
forwarding_rules = {}
user_states = {}
user_languages = {} # {user_id: 'en'}

# States for ConversationHandler
PHONE, CODE, PASSWORD = range(3)

# --- Translation Helper ---
def t(user_id, key, **kwargs):
    """Gets the translated string for a user."""
    lang = user_languages.get(user_id, 'en') # Default to English
    return translations.get(lang, translations['en']).get(key, f"_{key}_").format(**kwargs)

# --- Web Server to Keep Render Awake ---
flask_app = Flask(__name__)
@flask_app.route('/')
def index(): return "I am awake!"
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# --- Keyboards ---
def get_main_menu_keyboard(user_id):
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(t(user_id, "menu_manage_rules")), KeyboardButton(t(user_id, "menu_add_rule"))],
            [KeyboardButton(t(user_id, "menu_languages")), KeyboardButton(t(user_id, "menu_logout"))]
        ],
        resize_keyboard=True
    )

language_inline_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("🇬🇧 English", callback_data='set_lang_en'), InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data='set_lang_vi')],
    [InlineKeyboardButton("🇮🇳 हिंदी", callback_data='set_lang_hi'), InlineKeyboardButton("🇵🇹 Português", callback_data='set_lang_pt')],
    [InlineKeyboardButton("🇨🇳 简体中文", callback_data='set_lang_zh-cn'), InlineKeyboardButton("🇷🇺 Русский", callback_data='set_lang_ru')],
    [InlineKeyboardButton("🇺🇦 Ukrainian", callback_data='set_lang_uk'), InlineKeyboardButton("🇮🇩 Indonesian", callback_data='set_lang_id')]
])

async def get_rules_inline_keyboard(user_id):
    buttons = []
    user_rules = forwarding_rules.get(user_id, [])
    for i, rule in enumerate(user_rules):
        status_icon = "✅" if rule.get('active', False) else "❌"
        source_name = rule.get('source_name', str(rule['source']))
        dest_name = rule.get('destination_name', str(rule['destination']))
        buttons.append([
            InlineKeyboardButton(f"{status_icon} {source_name} ➡️ {dest_name}", callback_data=f'toggle_{i}'),
            InlineKeyboardButton("🗑️", callback_data=f'delete_{i}')
        ])
    return InlineKeyboardMarkup(buttons) if buttons else None

# --- Telethon Client & Forwarding Logic ---
def create_telethon_event_handler(user_id):
    @events.register(events.NewMessage)
    async def telethon_event_handler(event):
        # ... (This logic remains the same)
        normalized_chat_id = event.chat_id
        if event.is_channel and not str(normalized_chat_id).startswith('-100'):
            normalized_chat_id = int(f"-100{normalized_chat_id}")

        rules = forwarding_rules.get(user_id, [])
        for rule in rules:
            if rule['source'] == normalized_chat_id and rule.get('active', False):
                try:
                    await event.client.send_message(entity=rule['destination'], message=event.message)
                    logger.info(f"✅ User {user_id}: Forwarded from {rule['source_name']} to {rule['destination_name']}.")
                except Exception as e:
                    logger.error(f"❌ User {user_id}: Failed to forward message: {e}")
                return
    return telethon_event_handler

# --- Login & Logout Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_clients and user_clients[user_id].is_connected():
        await update.message.reply_text(t(user_id, "already_logged_in"), reply_markup=get_main_menu_keyboard(user_id))
        return ConversationHandler.END
    else:
        await update.message.reply_text(t(user_id, "welcome"), reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text(t(user_id, "phone_prompt"))
        return PHONE

async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text
    context.user_data['phone'] = phone
    session_name = f"user_{user_id}"
    client = TelegramClient(session_name, API_ID, API_HASH)
    context.user_data['client'] = client
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        await update.message.reply_text(t(user_id, "code_sent"))
        return CODE
    except Exception as e:
        logger.error(f"User {user_id}: Phone login error: {e}")
        await update.message.reply_text(t(user_id, "login_failed"))
        if client.is_connected(): await client.disconnect()
        return ConversationHandler.END

async def code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    match = re.match(r"mycode(\d+)", update.message.text.strip(), re.IGNORECASE)
    if not match:
        await update.message.reply_text(t(user_id, "code_invalid_format"))
        return CODE
    code = match.group(1)
    client = context.user_data['client']
    try:
        await client.sign_in(context.user_data['phone'], code, phone_code_hash=context.user_data['phone_code_hash'])
        await on_login_success(update, context)
        return ConversationHandler.END
    except Exception as e:
        if "password" in str(e).lower():
            await update.message.reply_text(t(user_id, "password_prompt"))
            return PASSWORD
        logger.error(f"User {user_id}: Code login error: {e}")
        await update.message.reply_text(t(user_id, "login_failed"))
        if client.is_connected(): await client.disconnect()
        return ConversationHandler.END

async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        client = context.user_data['client']
        await client.sign_in(password=update.message.text)
        await on_login_success(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"User {user_id}: Password login error: {e}")
        await update.message.reply_text(t(user_id, "login_failed"))
        if client.is_connected(): await client.disconnect()
        return ConversationHandler.END

async def on_login_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = context.user_data['client']
    user_clients[user_id] = client
    handler = create_telethon_event_handler(user_id)
    client.add_event_handler(handler)
    logger.info(f"Login successful for user {user_id}. Starting listener.")
    await update.message.reply_text(t(user_id, "login_success"), reply_markup=get_main_menu_keyboard(user_id))
    asyncio.create_task(client.run_until_disconnected())

async def cancel_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(t(user_id, "login_cancelled"), reply_markup=get_main_menu_keyboard(user_id))
    client = context.user_data.get('client')
    if client and client.is_connected():
        await client.disconnect()
    return ConversationHandler.END

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_clients:
        await update.message.reply_text("Logging out...")
        client = user_clients[user_id]
        await client.log_out()
        del user_clients[user_id]
        logger.info(f"User {user_id} logged out. Session terminated.")
        await update.message.reply_text(t(user_id, "logout_success"))
    else:
        await update.message.reply_text(t(user_id, "not_logged_in"))

# --- Language Selection Handler ---
async def show_languages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    lang_map = {'en': 'English', 'vi': 'Tiếng Việt', 'hi': 'हिंदी', 'pt': 'Português', 'zh-cn': '简体中文', 'ru': 'Русский', 'uk': 'Ukrainian', 'id': 'Indonesian'}
    current_lang_name = lang_map.get(lang_code, "English")
    text = t(user_id, "lang_menu_header", current_lang=current_lang_name)
    await update.message.reply_text(text, reply_markup=language_inline_keyboard)

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang_code = query.data.split('_')[-1]
    user_languages[user_id] = lang_code
    lang_map = {'en': 'English', 'vi': 'Tiếng Việt', 'hi': 'हिंदी', 'pt': 'Português', 'zh-cn': '简体中文', 'ru': 'Русский', 'uk': 'Ukrainian', 'id': 'Indonesian'}
    lang_name = lang_map.get(lang_code, "English")
    await query.edit_message_text(t(user_id, "lang_selected", lang_name=lang_name))
    # Update the main menu to reflect the new language
    await query.message.reply_text("Menu updated.", reply_markup=get_main_menu_keyboard(user_id))


# --- Rule Management Handlers ---
async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Match the button text against the user's current language
    if update.message.text == t(user_id, "menu_logout"):
        await logout(update, context)
    elif update.message.text == t(user_id, "menu_languages"):
        await show_languages(update, context)
    elif update.message.text == t(user_id, "menu_manage_rules"):
        keyboard = await get_rules_inline_keyboard(user_id)
        await update.message.reply_text(t(user_id, "rules_menu_header"), reply_markup=keyboard) if keyboard else await update.message.reply_text(t(user_id, "no_rules"))
    elif update.message.text == t(user_id, "menu_add_rule"):
        user_states[user_id] = 'awaiting_source'
        await update.message.reply_text(t(user_id, "add_rule_source_prompt"), reply_markup=ReplyKeyboardRemove())

async def handle_forwarded_message_for_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = user_states.get(user_id)
    if not state: return
    forward_info = update.message.forward_origin
    if not forward_info or not hasattr(forward_info, 'chat'):
        await update.message.reply_text(t(user_id, "invalid_forward"), reply_markup=get_main_menu_keyboard(user_id))
        del user_states[user_id]
        return
    chat_id = forward_info.chat.id
    chat_title = forward_info.chat.title or f"Chat {chat_id}"

    if state == 'awaiting_source':
        user_states[user_id] = {'state': 'awaiting_destination', 'source': chat_id, 'source_name': chat_title}
        await update.message.reply_text(t(user_id, "source_set", chat_title=chat_title))
    elif isinstance(state, dict) and state.get('state') == 'awaiting_destination':
        new_rule = {**state, 'destination': chat_id, 'destination_name': chat_title, 'active': True}
        del new_rule['state']
        forwarding_rules.setdefault(user_id, []).append(new_rule)
        del user_states[user_id]
        await update.message.reply_text(t(user_id, "rule_created"), reply_markup=get_main_menu_keyboard(user_id))
        keyboard = await get_rules_inline_keyboard(user_id)
        if keyboard: await update.message.reply_text(t(user_id, "rules_menu_header"), reply_markup=keyboard)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith('set_lang_'):
        await set_language(update, context)
        return
    await query.answer()
    user_id = query.from_user.id
    action, index_str = query.data.split('_')
    index = int(index_str)
    rules = forwarding_rules.get(user_id, [])

    if 0 <= index < len(rules):
        if action == 'toggle': rules[index]['active'] = not rules[index]['active']
        elif action == 'delete': del rules[index]
    
    keyboard = await get_rules_inline_keyboard(user_id)
    await query.edit_message_text(t(user_id, "rules_menu_header"), reply_markup=keyboard) if keyboard else await query.edit_message_text(t(user_id, "rules_deleted"))

# --- Application Lifecycle Hooks ---
async def post_init(application: Application):
    session_files = glob.glob("user_*.session")
    logger.info(f"Found {len(session_files)} existing session(s). Attempting to restore...")
    for session_file in session_files:
        try:
            user_id = int(re.search(r"user_(\d+).session", session_file).group(1))
            client = TelegramClient(session_file, API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                user_clients[user_id] = client
                handler = create_telethon_event_handler(user_id)
                client.add_event_handler(handler)
                asyncio.create_task(client.run_until_disconnected())
                me = await client.get_me()
                logger.info(f"✅ Successfully restored session for user {user_id} ({me.username}).")
            else:
                logger.warning(f"⚠️ Session file '{session_file}' is invalid or expired.")
        except Exception as e:
            logger.error(f"❌ Failed to restore session from '{session_file}': {e}")

async def on_shutdown(application: Application):
    logger.info("Shutting down... Disconnecting all Telethon clients.")
    for user_id, client in user_clients.items():
        if client.is_connected():
            await client.disconnect()
            logger.info(f"Disconnected client for user {user_id}.")

# --- Main ---
def main():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Flask web server started in a background thread.")
    
    application = Application.builder().token(BOT_TOKEN).build()

    application.post_init = post_init
    application.post_shutdown = on_shutdown

    login_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(filters.TEXT, phone_received)],
            CODE: [MessageHandler(filters.TEXT, code_received)],
            PASSWORD: [MessageHandler(filters.TEXT, password_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel_login)],
        conversation_timeout=300
    )
    application.add_handler(login_conv_handler)
    
    # This filter now uses a function to dynamically match translated button texts
    menu_texts = [translations[lang][key] for lang in translations for key in ["menu_manage_rules", "menu_add_rule", "menu_languages", "menu_logout"]]
    application.add_handler(MessageHandler(filters.Regex(f'^({"|".join(set(menu_texts))})$'), handle_menu_selection))
    
    application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_message_for_setup))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Starting bot...")
    application.run_polling()

if __name__ == '__main__':
    main()
