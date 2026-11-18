# Darkroom Exposure

Allows for you to find darkroom exposure times based on one print you've made.

- `api/` — FastAPI: reads EXIF, measures negative density, does the exposure math, stores the benchmark and completed prints (`api/data/`).
- `web/` — Node (no dependencies): serves the page and proxies `/api/*` to FastAPI.

## Run

```sh
docker compose up --build        # http://localhost:8080
```

## for web UI edits
```
docker compose up -d --build web
```
