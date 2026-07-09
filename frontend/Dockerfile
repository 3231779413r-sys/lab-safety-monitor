FROM node:22-slim AS base
WORKDIR /app
ENV PNPM_HOME=/pnpm
ENV PATH=${PNPM_HOME}:${PATH}
RUN corepack enable

FROM base AS deps
COPY package.json pnpm-lock.yaml .npmrc ./
COPY node_modules ./node_modules
RUN test -d node_modules/.pnpm || pnpm install --frozen-lockfile

FROM base AS builder
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN ./node_modules/.bin/next build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
COPY --from=builder /app/scripts ./scripts
EXPOSE 3000
CMD ["node", "scripts/start-with-log.js"]
