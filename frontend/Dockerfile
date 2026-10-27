FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
EXPOSE 5173
# Local development server only; production builds are deployed as static files (e.g. Vercel).
CMD ["npm", "run", "dev"]
