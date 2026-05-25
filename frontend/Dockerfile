# syntax=docker/dockerfile:1.7

# [LEARN] Frontend stays simple for dev — single stage, node hot-reload.
# Production build will be added in Month 3 (multi-stage: build → nginx static).

FROM node:20-alpine

WORKDIR /app

# package*.json copied separately so Docker can cache node_modules layer
# (only re-installs when manifest changes, not on every source edit).
COPY package*.json ./
RUN npm install || echo "no package.json yet — placeholder install"

COPY . .

EXPOSE 3000

CMD ["npm", "start"]
