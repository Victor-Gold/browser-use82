# X (Twitter) Automation with Browser-Use & Gemma 4

This directory contains a script (`x_automation.py`) that uses the [Browser-Use](https://github.com/browser-use/browser-use) framework to automatically follow and unfollow users on X (Twitter).

The script is configured to use:
1. **Your Local Chrome Browser**: It uses your existing Chrome profile so that it shares your active X.com login session. This prevents the agent from being blocked by login walls or captchas.
2. **Gemma 4**: It uses the online Gemma 4 model via Google's generative AI API to reason about the webpage and decide where to click.

## Prerequisites

Before running the script, ensure you have the following installed:

1. **Python 3.11+**
2. **uv** (Recommended Python package manager)
3. **Google API Key** (for accessing the online Gemma model)

## Setup Instructions

### 1. Install Dependencies

You need to install the required Python packages (`browser-use` and `python-dotenv`). The easiest way to do this is with `uv`.

```bash
# If using uv
uv pip install browser-use python-dotenv

# Or if using pip
pip install browser-use python-dotenv
```

Install the Playwright browser dependencies (required by browser-use):
```bash
uvx browser-use install
```

### 2. Configure Your API Key

Create a `.env` file in the same directory as the script and add your Google API key:

```env
GOOGLE_API_KEY=your_google_api_key_here
```

*Note: Ensure the model name `gemma-4` in the script matches the specific endpoint provided by your Google AI Studio or Vertex AI account.*

### 3. Configure Your Chrome Profile Paths

**CRITICAL**: You must configure the `browser` settings in `x_automation.py` to point to your specific computer's Chrome installation. Open `x_automation.py` and modify the `Browser(...)` initialization.

Here are the common paths depending on your operating system:

#### macOS
```python
executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
user_data_dir='~/Library/Application Support/Google/Chrome'
```

#### Windows
```python
executable_path='C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
user_data_dir='C:\\Users\\YOUR_USERNAME\\AppData\\Local\\Google\\Chrome\\User Data'
```
*(Make sure to replace `YOUR_USERNAME` with your actual Windows username)*

#### Linux
```python
executable_path='/usr/bin/google-chrome'
user_data_dir='~/.config/google-chrome'
```

If you use a specific Chrome profile (other than the main one), change `profile_directory='Default'` to the correct folder name (e.g., `'Profile 1'`).

## Running the Script

1. **Close all open Google Chrome windows.** The automation script cannot connect to your profile if Chrome is already running.
2. Run the script:

```bash
python x_automation.py
```

### How It Works

1. The script will open Chrome using your profile. You should see it go to `https://x.com` already logged in.
2. The agent takes a screenshot and extracts the HTML of the page.
3. It sends this information to the online Gemma 4 model.
4. Gemma 4 analyzes the page and decides the next action (e.g., "click the search bar", "type 'AI Agents'").
5. The agent executes the action in the browser.
6. This loop repeats until the task is complete (searching, following 2 users, navigating to your profile, and unfollowing 2 users).

## Customizing the Task

You can change what the agent does by modifying the `task_instructions` variable in `x_automation.py`. Just write out what you want it to do in plain English!
