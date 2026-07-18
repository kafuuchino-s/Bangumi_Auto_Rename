# Bangumi Auto Rename frontend

This directory contains the Vite + React 19 single-page console. Development
requests for `/api`, `/sendTask`, and `/health` are proxied to FastAPI on port
5999. The production build is written to `out/` and is served by FastAPI.

```bash
npm install
npm run dev
npm run lint
npm test
npm run i18n:check
npm run build
```

Routes are kept stable (`/`, `/logs`, `/subtitles`, and `/settings/*`) and are
resolved by React Router. FastAPI provides the HTML navigation fallback while
returning 404 for missing assets and reserved API paths.
