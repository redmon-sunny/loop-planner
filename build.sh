#!/bin/sh
# page.html is the source (artifact form, no document skeleton); index.html is what nginx serves.
python3 - <<'PY'
body=open('page.html',encoding='utf-8').read()
open('index.html','w',encoding='utf-8').write('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}</style>\n</head>\n<body>\n'+body+'\n</body>\n</html>\n')
PY
