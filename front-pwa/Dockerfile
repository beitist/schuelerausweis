# ============================================================================
# Hagen-ID PWA Frontend Dockerfile
# ============================================================================
# Multi-Stage Build: Node für Build, Nginx für Serving
#
# Für Coolify: Kann auch Nixpacks verwenden (automatisch erkannt)

# ----- Stage 1: Build -----
FROM node:20-alpine as builder

WORKDIR /app

# ============================================================================
# VITE_API_URL muss zur BUILD-Zeit gesetzt werden, nicht Runtime!
# In Coolify: Build Arguments -> VITE_API_URL = https://api.example.com (oder deine API-URL)
# ============================================================================
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

# Dependencies zuerst (für besseres Caching)
COPY package*.json ./
RUN npm install

# Source kopieren und bauen
COPY . .
RUN echo "Building with VITE_API_URL=$VITE_API_URL" && npm run build


# ----- Stage 2: Production -----
FROM nginx:1.27-alpine

# curl für Health-Check
RUN apk add --no-cache curl

# Nginx-Konfiguration für SPA (React Router) + PWA
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Build-Artefakte kopieren
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# Health-Check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
