import yaml
d = yaml.safe_load(open('/root/autodl-tmp/clash/sub.yaml', encoding='utf-8'))
pg = d.get('proxy-groups', [])
print('groups:', len(pg))
for g in pg:
    print('  ', g.get('type'), '|', g.get('name'), '| nodes=', len(g.get('proxies', [])))
print('total proxies:', len(d.get('proxies', [])))
print('mixed-port:', d.get('mixed-port'), '| controller:', d.get('external-controller'))
