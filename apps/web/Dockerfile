FROM node:24-alpine AS source

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY --chown=node:node . .
RUN chown -R node:node /app

USER node

FROM source AS development

EXPOSE 5173

CMD ["npm", "run", "dev"]

FROM source AS builder

COPY --from=erp_retained_assets --chown=node:node / /app/.asset-history/

ARG ERP_BUILD_ID
ARG ERP_RELEASE_ID
ARG ERP_WEB_ASSETS_PROTOCOL
ARG ERP_WEB_ASSET_INPUT_SHA256
ARG ERP_WEB_BUILD_ID
ENV ERP_BUILD_ID=${ERP_BUILD_ID}
ENV ERP_RELEASE_ID=${ERP_RELEASE_ID}
RUN if [ -n "$ERP_WEB_ASSETS_PROTOCOL$ERP_WEB_ASSET_INPUT_SHA256$ERP_WEB_BUILD_ID" ]; then \
      test "$ERP_WEB_ASSETS_PROTOCOL" = 1 && test -n "$ERP_WEB_BUILD_ID" && \
      node -e 'const fs=require("fs"),crypto=require("crypto"); const raw=fs.readFileSync("/app/.asset-history/manifest.json"); const m=JSON.parse(raw); if(!/^[a-f0-9]{64}$/.test(process.env.ERP_WEB_ASSET_INPUT_SHA256)||crypto.createHash("sha256").update(raw).digest("hex")!==process.env.ERP_WEB_ASSET_INPUT_SHA256||m.candidate_release_id!==process.env.ERP_WEB_BUILD_ID)process.exit(1)' && \
      export ERP_BUILD_ID="$ERP_WEB_BUILD_ID" ERP_RELEASE_ID="$ERP_WEB_BUILD_ID"; \
    fi && npm run build && node scripts/prepare-web-assets.mjs --merge-retained /app/.asset-history

FROM nginxinc/nginx-unprivileged:1.27-alpine AS production

LABEL cn.jingxue.erp.web-assets-contract="1"

USER root
RUN rm -rf /usr/share/nginx/html && mkdir -p /usr/share/nginx/html /etc/erp-web
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=101:101 /app/.build-metadata/assets-manifest.json /etc/erp-web/assets-manifest.json
COPY --from=builder --chown=101:101 /app/dist /usr/share/nginx/html

USER 101

EXPOSE 5173
