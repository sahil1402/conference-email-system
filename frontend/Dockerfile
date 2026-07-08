# Frontend image — Next.js 14, built and served in production mode.
FROM node:18-alpine

WORKDIR /app

# Install deps first for better layer caching (lockfile copied when present).
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

# NEXT_PUBLIC_* variables are inlined into the client bundle at BUILD time, so
# the API URL must be set before `npm run build`. It must point at an address
# the USER'S BROWSER can reach (the host), NOT the internal compose service
# name — the browser runs outside the docker network. Hence localhost, provided
# as a build arg by docker-compose.yml.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

EXPOSE 3000

# Bind to 0.0.0.0 so the port is reachable from the host.
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
