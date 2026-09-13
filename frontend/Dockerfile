# Imagen de producción del frontend.
# En desarrollo el frontend corre nativo con `npm run dev` (ver el
# comentario en docker-compose.yml); esta imagen solo se usa junto con
# docker-compose.prod.yml, detrás de Nginx.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Next.js "inlinea" las variables NEXT_PUBLIC_* en el bundle del cliente
# durante `next build`: tienen que existir como ARG/ENV en esta fase, no en
# runtime. Con Nginx como origen único (ver nginx/nginx.prod.conf), el valor
# por defecto es una ruta relativa: los fetch directos al backend (p.ej. el
# streaming SSE del chat) pasan por /api/... igual que el resto de llamadas,
# sin necesitar la URL absoluta que hacía falta en desarrollo sin proxy.
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
