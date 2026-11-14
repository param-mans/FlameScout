# FlameScout web (MDT + dispatcher console)

    npm install
    npm run dev      # http://localhost:5173, proxies /api and /ws to :8000
    npm run build

Currently renders mock S2 data from `src/mock/s2.ts`. Types in `src/types/schemas.ts` mirror
`packages/schemas` (update by hand when a Pydantic model changes).
