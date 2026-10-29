FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY server.mjs ./
USER node
ENV ONTOBRIDGE_MCP_HOST=0.0.0.0 ONTOBRIDGE_MCP_PORT=8790
EXPOSE 8790
CMD ["node", "server.mjs"]
