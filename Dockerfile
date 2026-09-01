FROM node:22-alpine
RUN npm install -g pnpm@10.33.0
WORKDIR /src
RUN git clone --depth 1 https://github.com/sipeed/picoclaw.git .
WORKDIR /src/web/frontend
RUN pnpm install --frozen-lockfile && pnpm run build:backend
