# Voc Studio React Frontend

This directory contains the React workspace for Voc Studio.

## Stack

- Vite
- React
- TypeScript
- Ant Design 5
- TanStack Query
- React Router hash routing
- Zustand

## Commands

```bash
npm install
npm run dev
npm run lint
npm run build
```

The development server proxies API and media requests to the default backend at `http://127.0.0.1:4200`.

`npm run build` writes production assets to `../app/static` so the existing FastAPI `/` route can serve the React app without backend API changes.

The previous single-file frontend is preserved as `app/static/legacy.html`.
