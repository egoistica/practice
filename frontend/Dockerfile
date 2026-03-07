FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

RUN addgroup -S appgroup \
    && adduser -S appuser -G appgroup -h /home/appuser \
    && mkdir -p /home/appuser \
    && chown -R appuser:appgroup /app /home/appuser

USER appuser

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
