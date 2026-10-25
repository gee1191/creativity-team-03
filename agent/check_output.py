"""Check a submission for repeats, broken citations and content not found in the cited paragraph."""
import json,re,sys,difflib
from collections import Counter
sub=json.load(open(sys.argv[1]))['requirements']
paras={}
for reg in {r['source']['regulation'] for r in sub}:
    for p in json.load(open(f"data/parsed/{reg.replace(' ','_')}__base.json")):
        paras.setdefault((reg,p['paragraph']),[]).append(p)
def norm(t):return re.sub(r'\W+',' ',t.lower()).strip()
# 1 repeats
exact=Counter((r['source']['regulation'],norm(r['text'])) for r in sub)
ex_d=sum(v-1 for v in exact.values() if v>1)
near=[]
by={}
for r in sub:by.setdefault(r['source']['regulation'],[]).append(r)
for reg,rs in by.items():
    for i in range(len(rs)):
        for j in range(i+1,len(rs)):
            a,b=norm(rs[i]['text']),norm(rs[j]['text'])
            if a!=b and abs(len(a)-len(b))<0.1*len(a) and difflib.SequenceMatcher(None,a,b).ratio()>0.97:near.append((rs[i],rs[j]))
# 2 citations
bad_label=[r for r in sub if (r['source']['regulation'],r['source']['paragraph']) not in paras]
bad_page=[r for r in sub if (r['source']['regulation'],r['source']['paragraph']) in paras and r['source']['page'] not in {p['page'] for p in paras[(r['source']['regulation'],r['source']['paragraph'])]} and abs(r['source']['page']-paras[(r['source']['regulation'],r['source']['paragraph'])][0]['page'])>1]
# 3 grounding: numbers and words of the requirement found in the cited paragraph (+ its parent, for resolved subjects)
STOP=set('the a an of to and or in on for with by be shall not that this which is are as at from any its their it than such each all where when while if then except unless'.split())
def src_text(reg,label):
    t=' '.join(p['text'] for p in paras.get((reg,label),[]))
    parent=label.rsplit('.',1)[0] if '.' in label else None
    if parent: t+=' '+' '.join(p['text'] for p in paras.get((reg,parent),[]))
    return t
nums_bad=[];low=[]
for r in sub:
    s=src_text(r['source']['regulation'],r['source']['paragraph']);sn=norm(s)
    nums=set(re.findall(r'\d+(?:[.,]\d+)?',r['text']))-set(re.findall(r'\d+(?:[.,]\d+)?',s))
    nums={n for n in nums if n not in r['source']['paragraph'] and not re.fullmatch(r'\d{1,2}',n) or n in ('',)}
    if nums:nums_bad.append((r,nums))
    w=[x for x in norm(r['text']).split() if x not in STOP and len(x)>2]
    if w:
        cov=sum(x in sn.split() for x in w)/len(w)
        if cov<0.6:low.append((round(cov,2),r))
print(f"requirements: {len(sub)}")
print(f"repeats: exact {ex_d}, near-identical pairs {len(near)}")
print(f"citations: unknown paragraph {len(bad_label)}, page not matching paragraph {len(bad_page)}")
print(f"numbers not in the cited paragraph: {len(nums_bad)}")
print(f"less than 60% of words found in the cited paragraph: {len(low)}")
for r,n in nums_bad[:8]:print('  NUM',r['id'],r['source']['regulation'],r['source']['paragraph'],n,'|',r['text'][:120])
for c,r in sorted(low,key=lambda x:x[0])[:8]:print('  LOW',c,r['id'],r['source']['regulation'],r['source']['paragraph'],'|',r['text'][:120])
for a,b in near[:3]:print('  NEAR',a['id'],b['id'],'|',a['text'][:90])
for r in bad_label[:5]:print('  LABEL',r['id'],r['source'])
