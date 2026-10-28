FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY static/ /usr/share/nginx/html/
COPY static/soundfont/sonivox.sf2 /usr/share/nginx/soundfont/sonivox.sf2

EXPOSE 80
