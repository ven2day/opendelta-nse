FROM node:22-bookworm-slim

WORKDIR /app

COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci

COPY nse_symbols_rsi_volume.csv ./nse_symbols_rsi_volume.csv
COPY strategy-parameters.json ./strategy-parameters.json
COPY web ./web

RUN cd web && npm run build \
    && mkdir -p dist/client/live \
    && mkdir -p .wrangler \
    && chown -R node:node .wrangler

WORKDIR /app/web
ENV NODE_ENV=production
ENV PORT=3000

EXPOSE 3000
USER node

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login',{signal:AbortSignal.timeout(3000)}).then(response=>{if(!response.ok)process.exit(1)}).catch(()=>process.exit(1))"]

CMD ["npm", "start"]
