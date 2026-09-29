# Vite dev server. The frontend is served by Vite in development; a production
# build would be static output from `npm run build` behind any web server.
FROM node:24-alpine

WORKDIR /app

# Install dependencies first so this layer caches independently of source.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

EXPOSE 5173

# --host binds 0.0.0.0 so the port is reachable from outside the container.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
