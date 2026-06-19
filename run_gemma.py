import asyncio
from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatGoogle

# Load API keys from .env
load_dotenv()

async def main():
    # Connect to your existing Chrome in debug mode
    browser = Browser(cdp_url="http://localhost:9222")

    # Configure the Google LLM to use Gemma 4 31B instruction-tuned model
    # The official model ID on Google API is "gemma-4-31b-it"
    llm = ChatGoogle(model="gemma-4-31b-it")

    task = "Go to https://news.ycombinator.com and tell me the top story."

    # Create the agent
    agent = Agent(task=task, browser=browser, llm=llm)

    # Run the task
    history = await agent.run()
    print("Final result:", history.final_result())

if __name__ == "__main__":
    asyncio.run(main())
