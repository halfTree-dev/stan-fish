FROM node:24-bookworm AS dev

RUN mkdir -p /app && chown node:node /app
WORKDIR /app
USER node

CMD ["sleep", "infinity"]

# 构建开发环境时
# 创建用户 node，并创建 /app

FROM node:24-bookworm AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .
RUN npm run build


FROM nginx:stable-alpine AS prod

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# 构建生产环境时
# 启用 Nginx 服务器，并针对构建产物 dist 中的内容拉起文件服务器
# 特别的。产物 dist/ 来自 build 镜像，build 镜像是一个中间工具，仅用于构建 /app/dist 以供取用
