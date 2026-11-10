# ============================================================================
# Hagen-ID Admin Frontend Dockerfile
# ============================================================================
# Multi-Stage Build: Node für Build, Nginx für Serving
#
# Für Coolify: Kann auch Nixpacks verwenden (automatisch erkannt)

# ----- Stage 1: Build -----
FROM node:24.22.2-alpine3.24 as builder

WORKDIR /app

# ============================================================================
# VITE_API_URL muss zur BUILD-Zeit gesetzt werden, nicht Runtime!
# In Coolify: Build Arguments -> VITE_API_URL = https://api.example.com (oder deine API-URL)
# ============================================================================
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

# Dependencies zuerst (für besseres Caching)
COPY package*.json ./
RUN npm ci

# Source kopieren und bauen
COPY . .
RUN echo "Building with VITE_API_URL=$VITE_API_URL" && npm run build


# ----- Stage 2: Production -----
# Exakt gepinnt (nginx-Patch + Alpine-Version); Bump per Commit, wenn der
# Wochenbericht ihn anmahnt. Stable-Linien haben eine gerade Minor-Version.
# (nginx:1.27-alpine blieb seit 04/2025 auf Alpine 3.21 stehen, inzwischen EOSL.)
FROM nginx:1.30.6-alpine3.24

# curl für Health-Check
RUN apk add --no-cache curl

# Nginx-Konfiguration für SPA (React Router)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Build-Artefakte kopieren
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# Health-Check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]