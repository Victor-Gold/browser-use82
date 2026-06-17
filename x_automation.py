import asyncio
import os
from browser_use import Agent, Browser
from browser_use.llm.google.chat import ChatGoogle
from dotenv import load_dotenv

# Load environment variables (e.g., GOOGLE_API_KEY)
load_dotenv()

async def main():
    # 1. Setup the Browser to use your local Chrome profile.
    # This allows the agent to use your existing login session for X.com, bypassing captchas.
    # IMPORTANT: You must close all existing Chrome windows before running this script.

    # Below are example paths for macOS.
    # Please update them according to your operating system (see README_X_AUTOMATION.md for paths).
    browser = Browser(
        # macOS Example:
        executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        user_data_dir=os.path.expanduser('~/Library/Application Support/Google/Chrome'),

        # Windows Example (uncomment and replace username):
        # executable_path='C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        # user_data_dir='C:\\Users\\YOUR_USERNAME\\AppData\\Local\\Google\\Chrome\\User Data',

        profile_directory='Default', # Or 'Profile 1', 'Profile 2' depending on your setup
    )

    # 2. Setup the LLM.
    # Using the online version of Gemma 4 via Google AI Studio.
    # Make sure your GOOGLE_API_KEY is set in your .env file or environment variables.
    llm = ChatGoogle(
        model="gemini-1.5-pro", # Note: Gemma 4 online is currently experimental or mapped to Gemini for general tools
        temperature=0.0
    )

    # 3. Define the Task for the Agent.
    # The agent will "see" the screen and navigate X.com.
    task_instructions = """
    1. Go to https://x.com.
    2. Ensure you are logged in. If you see a login screen, stop and report failure (the script should be run with a logged-in Chrome profile).
    3. Search for the keyword "AI Agents" using the search bar.
    4. Go to the "People" tab in the search results.
    5. Follow the first 2 users in the list by clicking their "Follow" button.
    6. Next, navigate to your own profile's "Following" list.
    7. Unfollow 2 users from the bottom of the list (or any 2 users you currently follow).
    8. Once complete, stop.
    """

    # 4. Initialize the Agent
    agent = Agent(
        task=task_instructions,
        browser=browser,
        llm=llm,
    )

    # 5. Run the Agent
    print("Starting X automation with Gemma 4...")
    history = await agent.run()

    print("\nAutomation Complete!")
    if history.is_successful():
        print("Task was successful.")
    else:
        print("Task encountered errors or did not finish completely.")

if __name__ == "__main__":
    asyncio.run(main())
