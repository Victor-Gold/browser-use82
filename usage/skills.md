# Skills Library

Skills are reusable prompt templates. Save a prompt once, load it into any agent task box.

## Using a skill

1. In the **Skills Library** card, select a skill from the dropdown
2. Click **Load → Agent 1** — the skill text appears in Agent 1's task input
3. Edit the text if needed (e.g. replace `[USERNAME]` placeholders)
4. Send

## Saving a skill

1. Type or refine a prompt in Agent 1's task input until it works well
2. In the Skills card, type a name in **Save as skill name**
3. Click **Save**
4. The dropdown updates immediately — skill is available for all future sessions

Skills are stored in `skills.json` at the project root.

## Built-in skills

### `x unfollow non-followers`
Unfollows everyone on X/Twitter who doesn't follow you back. Replace `[USERNAME]` with your handle before sending.

Key behaviors the agent knows to handle:
- Skips anyone with a "Follows you" label
- Clicks "Following" → confirms "Unfollow" in the dialog
- Recovers from "Something went wrong" popups by navigating back
- Scrolls to load more accounts
- Counts and reports total unfollowed

### `x follow back`
Follows back anyone in your followers list you aren't already following.

### `google research`
General research template. Replace `[TOPIC]` with what you want researched. Agent searches, opens top results, reads, and returns a structured summary.

## Tips

- Be specific — "click the Following button, then click Unfollow in the dialog" works better than "unfollow people"
- Include recovery steps for common errors the site might show
- Mention scroll behavior if the task requires loading more content
- For multi-step flows, describe each step explicitly
