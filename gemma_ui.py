import asyncio
import os
import sys

from dotenv import load_dotenv
import gradio as gr

from browser_use import Agent, Browser, ChatGoogle

# Load environment variables from .env
load_dotenv()

async def run_browser_task(
    task: str,
    api_key: str,
    model: str,
    use_debug_browser: bool,
    debug_port: str,
) -> str:
    # Use provided key or fallback to environment variable
    if not api_key.strip() and not os.getenv("GOOGLE_API_KEY"):
        return 'Error: Please provide a Google API key or ensure it is set in your .env file.'

    if api_key.strip():
        os.environ['GOOGLE_API_KEY'] = api_key

    try:
        browser = None
        if use_debug_browser:
            # Connect to your local Chrome debug instance
            browser = Browser(cdp_url=f"http://localhost:{debug_port}")
            
        # Initialize Google GenAI model
        llm = ChatGoogle(model=model)
        
        # Create and run the agent
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser
        )
        
        history = await agent.run()
        
        # Format the output gracefully
        final_result = history.final_result()
        if final_result:
            return f"Result: {final_result}"
        return "Agent finished, but didn't extract any specific text. Check your browser to see what it did!"
        
    except Exception as e:
        return f'Error: {str(e)}'

def create_ui():
    with gr.Blocks(title='Browser Use - Gemma Edition') as interface:
        gr.Markdown('# 🌐 Browser Use Frontend')
        gr.Markdown('This interface is pre-configured to use **Gemma 4 31B** and can automatically connect to your actual browser running in debug mode.')

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown('### ⚙️ Configuration')
                api_key = gr.Textbox(
                    label='Google API Key (optional if already in .env)', 
                    type='password'
                )
                model = gr.Dropdown(
                    choices=['gemma-4-31b-it', 'gemini-3-flash-preview', 'gemini-1.5-pro'], 
                    label='LLM Model', 
                    value='gemma-4-31b-it'
                )
                
                gr.Markdown('### 🖥️ Browser Settings')
                use_debug_browser = gr.Checkbox(label='Connect to Debug Browser (e.g. your Chrome)', value=True)
                debug_port = gr.Textbox(label='Debug Port', value='9222')
                
            with gr.Column(scale=2):
                gr.Markdown('### 🤖 Agent Task')
                task = gr.Textbox(
                    label='What should the agent do?',
                    placeholder='E.g., Go to news.ycombinator.com and summarize the top 3 posts...',
                    lines=4,
                )
                submit_btn = gr.Button('▶️ Run Task', variant='primary')
                
                gr.Markdown('### 📝 Results')
                output = gr.Textbox(label='Output', lines=8, interactive=False)

        submit_btn.click(
            fn=lambda *args: asyncio.run(run_browser_task(*args)),
            inputs=[task, api_key, model, use_debug_browser, debug_port],
            outputs=output,
        )

    return interface

if __name__ == '__main__':
    demo = create_ui()
    # Launching on 0.0.0.0 ensures it's accessible over local network if needed
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
