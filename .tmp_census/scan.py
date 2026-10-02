import sys, re, subprocess
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
ATS_KW=['greenhouse.io','smartrecruiters.com','workable.com','ashbyhq.com','lever.co','myworkdayjobs.com','hr.cloud.sap','paylocity.com','icims.com','bamboohr.com','trinethire.com','mokahr.com','ultipro.com','csod.com','isolvedhire.com','jobvite.com','eightfold.ai','phenom','rippling','talentio','breezy.hr','recruitee','zoho.com/recruit','adp.com','kezzler','applicantpro','jobaps','gupy','zhipin']
def scan(url, tmo=15):
    try:
        r=subprocess.run(["timeout",str(tmo+5),"curl","-sL","--max-time",str(tmo),"-A",UA,url],capture_output=True,timeout=tmo+8)
        t=r.stdout.decode("utf-8","ignore")
    except Exception:
        t=""
    print("===",url,"len",len(t))
    ti=re.findall(r'<title[^>]*>([^<]{0,90})',t)
    if ti: print("  title:",ti[0].strip()[:80])
    for k in ATS_KW:
        c=t.lower().count(k)
        if c:
            i=t.lower().find(k)
            frag=re.sub(r'\s+',' ',t[max(0,i-60):i+90]).replace('\n',' ')
            print("  ATS:",k,"x",c,"| ...",frag[:120])
    hrefs=re.findall(r'href="([^"]{1,140})"',t)
    sel=[h for h in hrefs if any(k in h.lower() for k in ('career','job','join','recruit','hr.'))]
    for h in list(dict.fromkeys(sel))[:8]: print("  link:",h)
    return t
if __name__=="__main__":
    for u in sys.argv[1:]: scan(u)
