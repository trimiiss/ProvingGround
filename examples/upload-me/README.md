# Drop this folder on the dashboard

Open **http://localhost:3000/upload** and drag this whole `upload-me` folder onto the page. The
three chatbots below join the fleet, take the same exam, and get a pass/blocked verdict. That takes
a few seconds.

| Bot | What it is | Expected verdict |
|---|---|---|
| Buggy shop bot | `bot.py` in this folder: says refunds take 10 days (it's 5) and leaks its secret to "admins" | ❌ blocked |
| Helpdesk bot | The demo support assistant on the premium model, reached over HTTP | ✅ pass |
| Kiosk bot | The demo kiosk on the budget model, reached over HTTP: falls for 2 of the 3 attacks | ❌ blocked |

What's inside:

* `provingground.yaml`: the bots (`targets`), the exams (`suites`) and the pass/fail rules (`gates`)
* `questions.jsonl`: questions with the facts a right answer must contain
* `attacks.jsonl`: messages that try to make a bot reveal its secret
* `bot.py`: the buggy shop bot's code

## Make it your own

1. Copy this folder and give `project:` a new name (re-uploading the same name replaces it).
2. Change `targets:` to your bot. For a bot behind a URL, set `url`, the JSON it expects
   (`request`) and where its answer is (`response`). For Python code, use `kind: python` like the
   shop bot. The [first-bot guide](../my-first-bot/README.md) explains every option.
3. Put your own questions in `questions.jsonl` and your bot's real secret code in `canaries:`.
4. Fix what the dashboard flags in your bot, then drop the folder again to re-test it.

Uploads that contain Python code run that code on the server. The local stack allows it
(`PGROUND_ALLOW_CODE_UPLOADS=true` in `docker-compose.yml`); a shared server should not, and then
accepts only bots reached over HTTP or plain models.
