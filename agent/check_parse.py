import json,re,sys
norm=lambda s: re.sub(r"[\s,]+"," ",s.lower()).strip().rstrip(".").replace(". "," ")
r=json.load(open('cases/dev-rear-lamp/expected.json'))['requirements']
miss=[];pagebad=[]
for x in r:
    s=x['source'];f='data/parsed/%s__base.json'%s['regulation'].replace(' ','_')
    recs=json.load(open(f))
    lab=norm(s['paragraph'].split(' Table')[0].split('. Table')[0])
    hits=[q for q in recs if norm(q['paragraph'])==lab]
    if not hits: miss.append((s['regulation'],s['paragraph'],s['page']))
    elif not any(abs(h['page']-s['page'])<=1 for h in hits): pagebad.append((s['regulation'],s['paragraph'],s['page'],[h['page'] for h in hits]))
print(len(r),'missing',len(miss),'page off',len(pagebad))
for m in sorted(set(miss)): print(' miss',m)
for m in pagebad: print(' page',m)
