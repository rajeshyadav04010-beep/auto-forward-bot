
import asyncio
from telethon import TelegramClient

# --- IMPORTANT ---
# Replace these with your own API credentials
API_ID = 11994009
API_HASH = "b8fce2b0baf980c696ce4eaeeb3dff07"
SESSION_NAME = "telethon_user_session"

async def main():
    print("Starting session generation...")
    # This will prompt you for your phone number, the code Telegram sends you,
    # and your 2FA password (if you have one) the first time you run it.
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    
    await client.start()
    
    me = await client.get_me()
    print(f"Login successful! Session file '{SESSION_NAME}.session' created for user: {me.username}")
    
    print("\nProcess finished. You can now upload the .session file to your bot's project directory.")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
