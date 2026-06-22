import asyncio
from dotenv import load_dotenv
from browser_use import Agent, ChatGoogle

# Load API keys from .env
load_dotenv()

async def main():
    llm = ChatGoogle(model="gemma-4-31b-it")

    task = "Go to https://news.ycombinator.com and tell me the top story."

    agent = Agent(task=task, llm=llm)

    # Run the task
    history = await agent.run()
    print("Final result:", history.final_result())

if __name__ == "__main__":
    asyncio.run(main())
