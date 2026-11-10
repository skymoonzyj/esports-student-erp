FROM node:24-alpine AS source

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY --chown=node:node . .
COPY --from=reactionx_contracts --chown=node:node /schema.json /fixtures.json /shared/reactionx/v1/
RUN chown -R node:node /app

USER node

FROM source AS development

EXPOSE 5173

CMD ["npm", "run", "dev"]

FROM source AS builder

RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS production

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=101:101 /app/dist /usr/share/nginx/html

USER 101

EXPOSE 5173
