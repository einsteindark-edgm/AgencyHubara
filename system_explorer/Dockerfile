# Multi-stage build: Node build → nginx serve.
#
# Build stage produce dist/ estático. Runtime stage es nginx:alpine que sirve
# dist/ y hace proxy de /api → http://api:8000 (servicio FastAPI en la red
# de docker-compose).

# ─── Stage 1: build ─────────────────────────────────────────────────────
FROM node:22-alpine AS builder
WORKDIR /app

# Layer cache: copia package*.json primero
COPY package.json ./
COPY package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copia el resto y buildea
COPY . .
RUN npm run build

# ─── Stage 2: nginx runtime ─────────────────────────────────────────────
FROM nginx:alpine
RUN rm -rf /usr/share/nginx/html/*
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# nginx default port
EXPOSE 80

# Healthcheck simple: index.html debe servirse OK
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
