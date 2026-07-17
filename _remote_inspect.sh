cd /root/autodl-tmp/clash
echo "=== proxy-groups block names ==="
python3 - <<'PY' 2>/dev/null || /root/miniconda3/bin/python - <<'PY'
import re,io
t=open('sub.yaml',encoding='utf-8').read()
import yaml
try:
    d=yaml.safe_load(t)
    pg=d.get('proxy-groups',[])
    print('groups:',len(pg))
    for g in pg:
        print(' ', g.get('type'), '|', g.get('name'), '| nodes=', len(g.get('proxies',[])))
    print('total proxies:', len(d.get('proxies',[])))
    print('mixed-port:', d.get('mixed-port'), 'controller:', d.get('external-controller'))
except Exception as e:
    print('yaml err', e)
PY
