
import asyncio
from pyrogram import Client

async def main():
    print("Starting Pyrogram session generation...")
    print("You will be prompted for your API ID, API hash, and phone number.")
    
    # Using ":memory:" tells Pyrogram not to create a .session file,
    # but to output a session string instead.
    async with Client(":memory:", api_id=11994009, api_hash="b8fce2b0baf980c696ce4eaeeb3dff07") as app:
        print("\nLogin successful!")
        
        session_string = await app.export_session_string()
        print("\n--- COPY THE STRING BELOW ---")
        print(session_string)
        print("--- END OF STRING ---")
        
        me = await app.get_me()
        print(f"\nSession created for user: {me.username}")
        print("Add this string to your .env file as PYROGRAM_SESSION_STRING")

if __name__ == "__main__":
    asyncio.run(main())
