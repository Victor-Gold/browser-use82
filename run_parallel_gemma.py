import asyncio
from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatGoogle

load_dotenv()

async def main():
    # NOTE: When running parallel agents, sharing a single debug browser (your Chrome) 
    # is experimental because agents might fight for focus across tabs.
    # It is highly recommended to let browser-use spin up its own headless contexts for parallel work.
    # If you still want to use your debug Chrome, use: Browser(cdp_url="http://localhost:9222")
    
    browser = Browser(headless=True) 
    llm = ChatGoogle(model="gemma-4-31b-it")

    tasks = [
        "Go to wikipedia.org and find the current population of Tokyo",
        "Go to github.com and summarize the top trending python repository",
        "Go to news.ycombinator.com and return the title of the top story"
    ]

    # Create an agent for each task. They will run in the same browser 
    # but use separate pages/tabs concurrently.
    agents = [
        Agent(task=task, llm=llm, browser=browser)
        for task in tasks
    ]

    print("🚀 Starting all agents in parallel...")
    
    # Run them all concurrently using asyncio.gather
    results = await asyncio.gather(*[agent.run() for agent in agents])
    
    for i, history in enumerate(results):
        print(f"\n--- Result for Task {i+1} ---")
        print(history.final_result())

    await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
