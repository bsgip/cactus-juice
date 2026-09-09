# cactus-juice frontend

Vite + React + TypeScript SPA for cactus-juice, using [Mantine](https://mantine.dev) for UI (including
`@mantine/charts` for future telemetry charting), [React Router](https://reactrouter.com) for multi-page
navigation, and [TanStack Query](https://tanstack.com/query) for talking to the backend JSON API.

## Running locally

1. Start the backend (from the repo root, with the `dev`/`test` extras installed and a configured
   `.env`/`JUICE_DATABASE_URL`):

   ```bash
   dotenv run -- uvicorn cactus_juice.main:create_app --factory --reload
   ```

2. Start the frontend dev server:

   ```bash
   npm install   # first time only
   npm run dev
   ```

   Vite proxies `/api/*` requests through to `http://localhost:8000` (see `vite.config.ts`), so the
   app can just call relative `/api/...` paths in both dev and a same-origin production deployment.

## Structure

- `src/api/` - typed wrappers around the backend JSON API (one module per resource, eg `config.ts`)
- `src/layout/` - the app shell (header + nav) shared by every page
- `src/pages/` - one component per route
- `src/theme.ts` - the Mantine theme

## Scripts

- `npm run dev` - start the dev server
- `npm run build` - typecheck (`tsc -b`) and produce a production build in `dist/`
- `npm run lint` - run oxlint
