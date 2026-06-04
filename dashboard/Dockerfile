FROM node:22-alpine

ARG VITE_API_URL=http://localhost:8000
ARG VITE_STORE_ID=ST1008
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_STORE_ID=${VITE_STORE_ID}

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "3000"]
