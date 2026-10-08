FROM node:20-alpine
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
# NEXT_PUBLIC_* is inlined at build time; fail the build if it is missing or
# points at the wrong (unrelated) backend instead of silently shipping a
# broken bundle.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
