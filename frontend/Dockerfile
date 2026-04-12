FROM node:22.22.2-alpine AS build

WORKDIR /app

ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

ARG VITE_API_BASE_URL=/v1
ARG VITE_REQUEST_TIMEOUT_MS=90000
ARG VITE_AUTH_RESET_ENABLED=true

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_REQUEST_TIMEOUT_MS=$VITE_REQUEST_TIMEOUT_MS
ENV VITE_AUTH_RESET_ENABLED=$VITE_AUTH_RESET_ENABLED

RUN pnpm run build

FROM nginx:1.28.3-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
