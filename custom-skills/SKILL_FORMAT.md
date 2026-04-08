# Custom Skills — Format Guide

> **Read this before creating any new skill.** This is the single format standard for all skills in this workspace. When asking OpenClaw/Frostbite to create a new skill, reference this file: "Follow the format in `/app/custom-skills/SKILL_FORMAT.md`."

---

## File Structure

Every skill is a folder inside `/home/resupaolo/openclaw-local/custom-skills/` (mounted as `/app/custom-skills/` inside Docker). The only required file is `SKILL.md`. Supporting files go in subfolders.

```
custom-skills/
  my-skill/
    SKILL.md           ← required, this is the skill
    references/        ← optional: static reference data (read-only)
    scripts/           ← optional: executable scripts called by the skill
    assets/            ← optional: JSON data files, configs
```

---

## SKILL.md Template

Copy this exactly. Replace the ALL_CAPS placeholders.

````skill
---
name: SKILL-NAME-KEBAB-CASE
description: ROUTING_DESCRIPTION
version: 1.0.0
metadata: { "openclaw": { "emoji": "EMOJI" } }
---

# SKILL TITLE

One sentence: what this skill does and when it's used.

## Core Context

Any permanent facts the skill needs (user details, portfolio data, configuration).
Keep this section SHORT — only include facts the LLM doesn't already know.
Do NOT duplicate data that exists in a reference file; point to the file instead.

## Workflow

Step-by-step procedure for every request of this type:

1. **Step one** — what to do first
2. **Step two** — what to do next
3. **Step three** — how to finalize

## Response Format

Show the expected output structure using a code block:

```
SECTION HEADER: [value]
→ Detail: [detail]
→ Reason: [reason]

RECOMMENDATION: [final advice]
```

## Notes

- Any edge cases or gotchas
- Tools this skill requires (web_search, web_fetch, exec, etc.)
- Any limits or restrictions
````

---

## Field Rules

### `name` (required)
- kebab-case, all lowercase
- Matches the folder name exactly
- No spaces, no underscores
- Examples: `travel-advisor`, `ph-credit-card-maximizer`, `epub-downloader`

### `description` (required — most important field)
This is what the LLM reads to decide whether to invoke this skill. Write it to **trigger on the right queries**.

**Rules:**
- Start with "Use when..." followed by the use cases
- End with "Triggers on: ..." followed by a quoted comma-separated list of keywords
- Include synonyms and natural phrasings the user would actually say
- Keep it to 2–3 sentences max — this is loaded for every message to decide routing

**Good example:**
```
description: Use when the user asks about travel planning, destinations, flights,
or itineraries. Triggers on: "travel", "flight", "trip", "itinerary", "visa",
"hotel", "booking", "Singapore", "Japan", "budget trip".
```

**Bad example (too vague — won't route correctly):**
```
description: Helps with travel.
```

### `version` (required)
- Semantic version string: `1.0.0`
- Increment when you significantly change the skill's behavior: `1.1.0`, `2.0.0`

### `metadata` (required)
- Always include the emoji: `{ "openclaw": { "emoji": "🔧" } }`
- Choose an emoji that matches the skill's domain

---

## Content Principles

### Be concise — the context window is finite
Every token in a SKILL.md costs context in every conversation where the skill is invoked. Challenge every paragraph: "Does the LLM need this to do the task correctly?"

- ✅ Specific workflows, exact API endpoints, quirky gotchas
- ✅ Response format templates (saves back-and-forth)
- ✅ Pointers to reference files instead of duplicating data
- ❌ Explaining what the LLM already knows (no "remember to be helpful")
- ❌ Duplicating card/portfolio/preference data that's in a reference file
- ❌ Long motivational text about the agent's role

### Reference files over inline data
If a skill needs a large dataset (card portfolio, product catalog, travel destinations), put it in a `references/` or `assets/` file and point to it:

```markdown
Card data: `/app/custom-skills/credit-card-advisor/CARDS.md`
```

### Response format templates
Always include a response format section. It removes ambiguity and produces consistent outputs without needing to specify format on every query.

---

## Current Skills Reference

| Skill | Emoji | Primary Use |
|---|---|---|
| `self-admin` | 🔧 | System health checks, restart/rebuild, troubleshooting, architecture |
| `ph-credit-card-maximizer` | 💳 | Best card for a purchase, rewards maximization, promo finding |
| `ph-investment-advisor` | 📈 | PH financial planning, digital banks, MP2, ETFs, REITs |
| `travel-advisor` | ✈️ | Trip planning, flights from PH, itineraries, Maps links |
| `media-downloader` | 📥 | Routing to epub/audiobook download |
| `epub-downloader` | 📚 | Download free EPUBs from Gutenberg/Archive/OpenLibrary |
| `finance-tracker` | 💰 | Live personal finance data — expenses, income, account balances |
| `nutrition-tracker` | 🥗 | Live calorie & macro tracker — log food, check daily intake, goals |
| `calendar-assistant` | 📅 | Google Calendar events, weekly digest, scheduling |

---

## Step-by-Step: Creating a New Skill

1. **Create the folder:**
   ```bash
   mkdir /home/resupaolo/openclaw-local/custom-skills/my-skill
   ```

2. **Create SKILL.md** using the template above

3. **Verify formatting:**
   - Frontmatter block starts and ends with `---`
   - File is wrapped in ` ```skill ` ... ` ``` ` fences (OpenClaw requirement)
   - `name` matches folder name exactly
   - `description` ends with `Triggers on: "word1", "word2", ...`

4. **Restart OpenClaw** to reload skills:
   ```bash
   cd /home/resupaolo/openclaw-local && docker compose restart openclaw
   ```

5. **Test routing** — send a message that should trigger the skill. If it doesn't invoke automatically, check that the trigger keywords in `description` match what you said.

---

## Common Mistakes

| Mistake | Fix |
|---|---|
| Forgetting the ` ```skill ` fence | Wrap the entire SKILL.md body in ` ```skill ` ... ` ``` ` |
| `name` doesn't match folder | Rename one to match — OpenClaw uses both |
| Vague `description` — skill not triggered | Add more specific trigger phrases to `Triggers on:` |
| Duplicating data from CARDS.md inline | Replace with a file reference |
| Forgetting to restart OpenClaw | Skills aren't hot-reloaded — always restart after changes |
| Skill too long (>300 lines) | Split into a main SKILL.md + supporting `references/` files |
