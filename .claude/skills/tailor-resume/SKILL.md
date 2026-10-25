---
name: tailor-resume
description: Write resume bullet suggestions for the jobs on JobPrep's Tailor page (up to 10) and import them so the app re-scores the match. Use when the user says "tailor my resume", "suggest bullets", "/tailor-resume", or wants suggestions for the jobs in their tailoring list. Never invents experience; never creates resume files.
---

# Tailor resume suggestions for JobPrep

JobPrep (local server, normally `http://127.0.0.1:8000`) already analyzes each job against the resume and builds a
prompt with the missing keywords, weak requirements, every resume bullet (ids like `E1.2`) and the reply format.
Your job: answer that prompt honestly for each job and import the answer. The user reviews every suggestion in the app.

## Steps

1. List the tailoring jobs:
   ```
   curl -s http://127.0.0.1:8000/tailor
   ```
   Work on the jobs the user named; otherwise every job with `"suggestions": 0`. If the server isn't running, tell
   the user to start it (`.venv/bin/uvicorn backend.api:app`) and stop.
2. For each job, fetch its prompt:
   ```
   curl -s "http://127.0.0.1:8000/tailor/<id>/prompt"
   ```
   (URL-encode the id — it can contain `:`.) Read it fully. It is the source of truth for the rules and JSON shape.
3. Write the reply JSON yourself, following the prompt exactly:
   - **Truth only.** Every bullet must be supported by the user's existing bullets/skills in the prompt. Rephrase,
     reorder and use the job's exact wording for things the user really did — never add employers, tools, numbers,
     or results that aren't there. Anything the job wants that the resume can't support goes in `"gaps"`.
   - Prefer `rewrite` of an existing bullet (keep its facts and numbers) over `new`. At most 8 suggestions.
   - One line each (≤ 30 words), strong action verb, no keyword stuffing.
   - `skills_to_add` only lists skills already evidenced in the bullets or the other resume ("on my other resume").
4. Import it (write the JSON to a temp file in the job's tmp dir first to avoid shell-quoting problems):
   ```
   python3 -c 'import json,sys; print(json.dumps({"text": open(sys.argv[1]).read()}))' /path/reply.json \
     | curl -s -X POST -H 'Content-Type: application/json' --data @- "http://127.0.0.1:8000/tailor/<id>/suggestions"
   ```
   The response contains `now.score` and `with_all.score` (match if every suggestion were accepted).
5. Report per job: title/company, match now → if all accepted, how many suggestions, and the gaps — then remind the
   user to review and accept/reject each one on the **Tailor** page (http://127.0.0.1:8000/#tailor). Do not accept
   suggestions on the user's behalf and do not edit their resume files.
