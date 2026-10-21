# Chat API (optional)

The website's Ask box works without this: it answers from the data in the browser.
This API adds a free-form AI assistant that can handle more complex questions.

The language model is only allowed to call database tools:
- `search_indicators`, `indicator_info`
- `resolve_geo`
- `get_series`, `trend`, `compare`, `rank`
- `search_news`

Every number in its reply is checked against the tool results. A reply containing a figure no tool returned is sent back once for correction, and withheld if it still fails.

## Deploy free on Hugging Face Spaces

1. Get a free API key from [Groq](https://console.groq.com) (no card needed). Any OpenAI-compatible provider with tool calling also works.
2. Create a new Space at https://huggingface.co/new-space and choose **Docker** as the SDK.
3. Upload the contents of this `api/` folder: `Dockerfile`, `requirements.txt` and `gse_api/`.
4. In the Space's **Settings → Variables and secrets**, add:
   - `LLM_API_KEY`: your Groq key. This must be a secret.
   - `ALLOWED_ORIGINS`: `https://sahiljangra12.github.io`
   - Optional: `LLM_MODEL` (default `llama-3.3-70b-versatile`) and `LLM_BASE_URL` (default Groq).
5. When the Space is running, open `https://<your-space>.hf.space/health`. It should report the number of indicators and `"llm_configured": true`.
6. In `site/assets/config.js`, set `apiUrl` to `https://<your-space>.hf.space`, then commit. The site switches to the assistant and falls back to the built-in engine if the API is down.

The API downloads the latest data bundle from this repo's `data-latest` release at start-up and again every 12 hours (`DATA_REFRESH_HOURS`).

## Run locally

```bash
pip install -r requirements.txt
LLM_API_KEY=... uvicorn gse_api.app:app --port 8000
# or against a local build:  LOCAL_DATA_DIR=../build/data uvicorn gse_api.app:app --port 8000
```

## Tests and evaluation

```bash
pip install pytest && pytest                                         # tools and the number check, no network
LLM_API_KEY=... python eval/run_eval.py --data ../build/data         # scores real answers
```

`eval/questions.yaml` is a seed set of 12 questions. Add the questions real users ask, aiming for about 100.

## Limits

- Groq's free tier caps requests per minute and per day. The API caches repeated questions and rate-limits each visitor to 12 questions a minute (`RATE_LIMIT_PER_MINUTE`).
- Free Spaces sleep when idle, so the first question after a quiet period can take a minute.
- Questions are sent to the model provider. The site tells users this when the assistant is on.
