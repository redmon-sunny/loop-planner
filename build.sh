#!/bin/sh
# page.html is the source (artifact form, no document skeleton); public/index.html is what nginx serves.
# public/ is a directory on purpose: a single-file bind mount keeps pointing at the old inode after git replaces
# the file, so nginx would serve a stale page until the container is recreated. Directory mounts follow renames.
mkdir -p public
python3 - <<'PY'
body=open('page.html',encoding='utf-8').read()
open('public/index.html','w',encoding='utf-8').write('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}</style>\n</head>\n<body>\n'+body+'\n</body>\n</html>\n')
PY
echo "built public/index.html"
