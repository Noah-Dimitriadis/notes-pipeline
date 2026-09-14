# M10 — placeholder image shared by the `web` and `admin` compose services.
#
# The real Next.js app (M14 for `web`, M15 for `admin` — same codebase, two
# entrypoints) doesn't exist yet. Until then this just proves the compose
# network topology (which interface each service binds to) is correct.
# M14 should replace this Dockerfile entirely rather than build on it.

FROM nginx:alpine
COPY placeholder.html /usr/share/nginx/html/index.html
RUN adduser -D -H notes \
    && chown -R notes:notes /var/cache/nginx /var/run \
    && sed -i 's/listen\s*80;/listen 3000;/' /etc/nginx/conf.d/default.conf
USER notes
EXPOSE 3000
