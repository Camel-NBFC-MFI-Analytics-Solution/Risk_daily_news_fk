# -*- coding: utf-8 -*-
"""Risk Daily News - single-file Streamlit app.
Focus: India Microfinance, JLG/SHG, NBFC and Climate Risk. Bank news excluded.
Includes dedicated Moneycontrol coverage for NBFC/MFI companies and sector terms.
Run: python -m streamlit run risk_daily_news.py --server.port 8501 --server.address 0.0.0.0
"""
import re, ssl, json, html, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import pandas as pd
import streamlit as st

IST=timezone(timedelta(hours=5,minutes=30)); LOOKBACK_HOURS=48; TIMEOUT=18; MAX_WORKERS=12; REFRESH_SECONDS=300
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
STREAM_MF="Microfinance / JLG / NBFC"; STREAM_CLIMATE="Climate Risk"
st.set_page_config(page_title="Risk Daily News",page_icon="📰",layout="wide")
st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',unsafe_allow_html=True)

MF_QUERIES=[
 "microfinance India","microfinance institutions MFI India","NBFC-MFI India","MFIN microfinance","Sa-Dhan microfinance","RBI microfinance",
 "JLG loans India",'"joint liability group" loans India','"self help group" microfinance India',"microfinance stress delinquency NPA default",
 "microfinance collection efficiency India","microfinance guardrails India","microfinance ordinance India","CGFMU microfinance",
 "CreditAccess Grameen","Spandana Sphoorty","Fusion Finance microfinance","Satin Creditcare","Muthoot Microfin","Asirvad Microfinance",
 "Arman Financial","Annapurna Finance microfinance","Svatantra Microfin","Belstar Microfinance","NBFC India RBI regulation",
 "NBFC asset quality NPA stress India","NBFC funding NCD borrowing India","NBFC rating downgrade India","NBFC unsecured loans India","gold loan NBFC India"
]
CLIMATE_QUERIES=["climate risk India","IMD red alert India","IMD orange alert India","flood India district","cyclone India landfall","heatwave India",
 "drought India","monsoon deficit India","excess rainfall India","unseasonal rain crop damage India","crop loss farmers rainfall India",
 "cloudburst landslide India","climate risk microfinance NBFC India","RBI climate risk NBFC","kharif rabi sowing weather India"]

# Dedicated Moneycontrol searches. These use Google News with site:moneycontrol.com so the app
# captures Moneycontrol stories even when the generic Moneycontrol RSS feed does not surface them.
MONEYCONTROL_QUERIES=[
 'site:moneycontrol.com microfinance India','site:moneycontrol.com MFI India','site:moneycontrol.com NBFC India',
 'site:moneycontrol.com NBFC-MFI','site:moneycontrol.com JLG microfinance','site:moneycontrol.com microfinance NPA stress',
 'site:moneycontrol.com NBFC NPA asset quality','site:moneycontrol.com NBFC RBI regulation',
 'site:moneycontrol.com NBFC funding NCD borrowing','site:moneycontrol.com NBFC rating downgrade upgrade',
 'site:moneycontrol.com unsecured lending NBFC','site:moneycontrol.com gold loan NBFC',
 'site:moneycontrol.com "CreditAccess Grameen"','site:moneycontrol.com "Spandana Sphoorty"',
 'site:moneycontrol.com "Fusion Finance"','site:moneycontrol.com "Satin Creditcare"',
 'site:moneycontrol.com "Muthoot Microfin"','site:moneycontrol.com "Asirvad Microfinance"',
 'site:moneycontrol.com "Arman Financial"','site:moneycontrol.com "Annapurna Finance"',
 'site:moneycontrol.com "Svatantra Microfin"','site:moneycontrol.com Manappuram NBFC',
 'site:moneycontrol.com "Bajaj Finance" NBFC','site:moneycontrol.com "Shriram Finance" NBFC',
 'site:moneycontrol.com Cholamandalam NBFC','site:moneycontrol.com "Muthoot Finance" NBFC',
 'site:moneycontrol.com "Mahindra Finance" NBFC','site:moneycontrol.com "L&T Finance" NBFC',
 'site:moneycontrol.com "IIFL Finance" NBFC'
]
DIRECT_FEEDS={
 "Economic Times":"https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13358259.cms",
 "Business Standard":"https://www.business-standard.com/rss/finance-103.rss",
 "Moneycontrol":"https://www.moneycontrol.com/rss/business.xml",
 "Financial Express":"https://www.financialexpress.com/money/banking-finance/feed/",
 "RBI":"https://www.rbi.org.in/pressreleases_rss.xml"
}
MARKET_SYMBOLS={"NIFTY 50":"^NSEI","CreditAccess Grameen":"CREDITACC.NS","Spandana Sphoorty":"SPANDANA.NS","Fusion Finance":"FUSION.NS",
 "Satin Creditcare":"SATIN.NS","Muthoot Microfin":"MUTHOOTMF.NS","Arman Financial":"ARMANFIN.NS","Manappuram":"MANAPPURAM.NS",
 "Bajaj Finance":"BAJFINANCE.NS","Shriram Finance":"SHRIRAMFIN.NS","Cholamandalam Fin":"CHOLAFIN.NS","Muthoot Finance":"MUTHOOTFIN.NS",
 "M&M Financial":"M&MFIN.NS","L&T Finance":"LTF.NS","IIFL Finance":"IIFL.NS"}

MF_RE=re.compile(r"micro\s?-?financ|\bmfis?\b|nbfc|micro\s?-?lend|\bjlg\b|joint liability|\bshgs?\b|self[- ]help group|\bmfin\b|sa-?dhan|creditaccess|spandana|fusion finance|satin creditcare|muthoot microfin|asirvad|arman financial|annapurna finance|svatantra|belstar|cgfmu|microcredit|gold loan|non-?banking financ|bajaj finance|shriram finance|cholamandalam|muthoot finance|manappuram|mahindra finance|l&t finance|iifl",re.I)
CLIMATE_RE=re.compile(r"climate|flood|cyclone|heat ?wave|drought|monsoon|rainfall|heavy rain|unseasonal|cloudburst|landslide|\bimd\b|red alert|orange alert|weather|hailstorm|crop (?:loss|damage|failure)|kharif|rabi|sowing|extreme weather|temperature",re.I)
BANK_HARD_RE=re.compile(r"\bsbi\b|state bank|hdfc bank|icici bank|axis bank|kotak|yes bank|indusind|idfc first|bandhan bank|ujjivan|equitas|suryoday|utkarsh|esaf|small finance bank|\bsfbs?\b|bank of baroda|punjab national|\bpnb\b|canara bank|union bank|bank of india|indian bank|federal bank|rbl bank|idbi|payments bank|co-?operative bank|regional rural bank|\brrbs?\b",re.I)
BANK_SOFT_RE=re.compile(r"\bbanks?\b|\bbanking\b|\bbanker",re.I); BANK_KEEP_RE=re.compile(r"reserve bank(?: of india)?|world bank",re.I)
CATS={"Regulatory & Policy":r"\brbi\b|reserve bank|regulat|guideline|circular|ordinance|policy|framework|directive|norms?|penalt|government",
 "Asset Quality & Stress":r"\bnpa|gnpa|delinquen|\bpar\s?\d|default|write-?off|stress|overdue|slippage|credit cost|provision|collection efficiency|asset quality|over-?indebted|\bdpd\b|loss",
 "Fraud & Conduct":r"fraud|coercive|harass|suicide|recovery agent|\bfir\b|arrest|scam|mis-?sell|complaint|protest|raid|cheat",
 "Funding & Ratings":r"fund ?rais|\bncds?\b|\bdebt\b|equity|securiti|borrowing|rating|downgrade|upgrade|icra|crisil|liquidity|capital|sidbi|nabard",
 "Business & Results":r"disburs|\baum\b|loan book|growth|branch|customer|results?|profit|quarter|revenue|margin|share price|stock|merger"}
CCATS={"Climate - Flood & Rain":r"flood|heavy rain|rainfall|cloudburst|landslide|inundat|waterlog|unseasonal","Climate - Cyclone & Storm":r"cyclone|storm|hailstorm|landfall|depression","Climate - Heat & Drought":r"heat ?wave|drought|dry spell|temperature|water crisis|deficit","Climate - Agri & Crop":r"crop|kharif|rabi|sowing|farmer|harvest|agri","Climate - Policy & Finance":r"\brbi\b|disclosure|\besg\b|green financ|carbon|emission|policy|framework"}
HIGH=re.compile(r"default|fraud|downgrade|stress|\bnpa|gnpa|delinquen|write-?off|crisis|waiver|protest|penalt|raid|arrest|slippage|scam",re.I)
MED=re.compile(r"\brbi\b|regulat|rating|provision|caution|warn|slowdown|declin|fall|drop|guardrail|concern|pressure",re.I)
CLIM_HIGH=re.compile(r"red alert|flood|cyclone|cloudburst|landslide|severe|extreme|drought|heat ?wave|crop (?:loss|damage|failure)|death|evacuat|disaster",re.I)
CLIM_MED=re.compile(r"orange alert|yellow alert|warning|deficit|unseasonal|heavy rain|dry spell|forecast|risk",re.I)
NUM_RE=re.compile(r"(?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?\s?(?:lakh crore|crore|cr\b|lakh|billion|million)?|\d+(?:\.\d+)?\s?(?:%|bps|basis points|mm\b|cm\b|°c)",re.I)
STATES=["Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh","Goa","Gujarat","Haryana","Himachal Pradesh","Jharkhand","Karnataka","Kerala","Madhya Pradesh","Maharashtra","Manipur","Meghalaya","Mizoram","Nagaland","Odisha","Punjab","Rajasthan","Sikkim","Tamil Nadu","Telangana","Tripura","Uttar Pradesh","Uttarakhand","West Bengal","Delhi","Jammu and Kashmir","Ladakh","Puducherry"]

opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
def get(url,retries=2):
    err=None
    for i in range(retries+1):
        try:
            with opener.open(urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"*/*"}),timeout=TIMEOUT) as r:return r.read()
        except Exception as e:err=e;time.sleep(2 if i<retries else 0)
    raise err
def google_rss(q):return "https://news.google.com/rss/search?q="+urllib.parse.quote(q+" when:2d")+"&hl=en-IN&gl=IN&ceid=IN:en"
def clean(s):return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s or ""))).strip()
def dt(s):
    if not s:return datetime.now(IST)
    try:d=parsedate_to_datetime(s)
    except Exception:
        try:d=datetime.fromisoformat(s.replace("Z","+00:00"))
        except Exception:return datetime.now(IST)
    if d.tzinfo is None:d=d.replace(tzinfo=IST)
    return d.astimezone(IST)
def text(e,t):
    x=e.find(t);return (x.text or "").strip() if x is not None and x.text else ""
def parse(raw,name,hint,is_google,force_source=None):
    try:root=ET.fromstring(raw)
    except ET.ParseError:root=ET.fromstring(raw[raw.find(b"<"):])
    atom="{http://www.w3.org/2005/Atom}";items=list(root.iter("item")) or list(root.iter(atom+"entry"));out=[]
    for x in items:
        if x.tag=="item":
            title=clean(text(x,"title"));link=text(x,"link");pub=text(x,"pubDate");desc=clean(text(x,"description"));se=x.find("source");src=(se.text or "").strip() if se is not None and se.text else name
        else:
            title=clean(text(x,atom+"title"));le=x.find(atom+"link");link=le.get("href","") if le is not None else "";pub=text(x,atom+"updated") or text(x,atom+"published");desc=clean(text(x,atom+"summary"));src=name
        if not title or not link:continue
        if is_google:
            if src and title.endswith(" - "+src):title=title[:-(len(src)+3)].strip()
            elif " - " in title:title,src=title.rsplit(" - ",1)
            desc=""
        if force_source:src=force_source
        out.append({"title":title,"link":link,"source":src,"published":dt(pub),"summary":desc[:300],"hint":hint})
    return out
def is_bank(t):
    x=BANK_KEEP_RE.sub(" ",t);return bool(BANK_HARD_RE.search(x) or (BANK_SOFT_RE.search(x) and not MF_RE.search(x)))
def best(mp,t,default):
    n,c=max((len(re.findall(rx,t,re.I)),c) for c,rx in mp.items());return c if n else default
def enrich(a):
    t=a["title"]+" "+a["summary"]
    if is_bank(t):return None,"bank"
    mf=bool(MF_RE.search(t));cl=bool(CLIMATE_RE.search(t))
    if a["hint"]==STREAM_MF and mf:s=STREAM_MF
    elif a["hint"]==STREAM_CLIMATE and cl:s=STREAM_CLIMATE
    elif mf:s=STREAM_MF
    elif cl:s=STREAM_CLIMATE
    else:return None,"irrelevant"
    if s==STREAM_CLIMATE:cat=best(CCATS,t,"Climate - General");sev="High" if CLIM_HIGH.search(t) else "Medium" if CLIM_MED.search(t) else "Low"
    else:cat=best(CATS,t,"General / Sector");sev="High" if HIGH.search(t) else "Medium" if MED.search(t) else "Low"
    a.update(stream=s,category=cat,severity=sev,numbers=list(dict.fromkeys(m.group(0) for m in NUM_RE.finditer(t)))[:5],states=[x for x in STATES if re.search(r"\b"+re.escape(x)+r"\b",t,re.I)])
    return a,None
def norm(t):return re.sub(r"[^a-z0-9]","",t.lower())[:90]

@st.cache_data(ttl=1800,show_spinner=False)
def fetch_news():
    jobs=[(f"Google: {q}",google_rss(q),STREAM_MF,True,None) for q in MF_QUERIES]
    jobs += [(f"Moneycontrol: {q}",google_rss(q),STREAM_MF,True,"Moneycontrol") for q in MONEYCONTROL_QUERIES]
    jobs += [(f"Google: {q}",google_rss(q),STREAM_CLIMATE,True,None) for q in CLIMATE_QUERIES]
    jobs += [(n,u,None,False,None) for n,u in DIRECT_FEEDS.items()]
    raw=[];ok=failed=0
    def work(j):return parse(get(j[1]),j[0],j[2],j[3],j[4])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fs=[ex.submit(work,j) for j in jobs]
        for f in as_completed(fs):
            try:raw.extend(f.result());ok+=1
            except Exception:failed+=1
    now=datetime.now(IST);cut=now-timedelta(hours=LOOKBACK_HOURS);seen=set();news=[];banks=0
    for a in raw:
        if not cut<=a["published"]<=now+timedelta(hours=2):continue
        a,why=enrich(a)
        if why=="bank":banks+=1
        if not a:continue
        k=norm(a["title"])
        if k in seen:continue
        seen.add(k);a.pop("hint",None);news.append(a)
    news.sort(key=lambda x:x["published"],reverse=True)
    return news,{"ok":ok,"failed":failed,"banks_dropped":banks,"moneycontrol":sum(1 for a in news if a["source"].lower()=="moneycontrol")}
def quote(item):
    name,sym=item
    try:
        u=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d";r=json.loads(get(u,1))["chart"]["result"][0];cs=[c for c in r["indicators"]["quote"][0]["close"] if c is not None];p=r["meta"].get("regularMarketPrice") or cs[-1];prev=cs[-2] if len(cs)>1 else r["meta"].get("chartPreviousClose");return {"Company / Index":name,"LTP":p,"Change":p-prev if prev else 0,"Change %":(p-prev)/prev*100 if prev else 0}
    except:return None
@st.cache_data(ttl=900,show_spinner=False)
def markets():
    with ThreadPoolExecutor(max_workers=6) as ex:rs=[r for r in ex.map(quote,MARKET_SYMBOLS.items()) if r]
    return rs

st.markdown("""<style>.block-container{padding-top:1rem;max-width:1500px}.hero{background:linear-gradient(120deg,#0b2545,#13315c 55%,#8d1b3d);color:#fff;padding:18px 26px;border-radius:13px}.hero h1{margin:0;color:#fff}.card{background:#fff;border-radius:10px;padding:12px 15px;margin:8px 0;box-shadow:0 1px 5px #0002;border-left:5px solid #2a9d8f}.card.High{border-left-color:#e63946}.card.Medium{border-left-color:#f4a261}.card a{color:#0b2545!important;font-weight:650;text-decoration:none}.meta{color:#64748b;font-size:12px;margin-bottom:5px}.badge{padding:2px 7px;margin-right:5px;border-radius:10px;background:#e3ecf7;font-size:11px;font-weight:700}.mc{background:#fff0d8;color:#9a3412}.clim{background:#d8f3dc;color:#1b4332}.num{background:#0b2545;color:#fff}.state{background:#f1e6f9;color:#5a189a}div[data-testid="stMetric"]{background:white;border-radius:10px;padding:9px 13px;box-shadow:0 1px 5px #0002;border-top:4px solid #13315c}</style>""",unsafe_allow_html=True)
now=datetime.now(IST);st.markdown(f'<div class="hero"><h1>📰 Risk Daily News</h1><div>Microfinance · JLG/SHG · NBFC · Climate Risk · Dedicated Moneycontrol coverage | Banks excluded</div><b>{now:%A, %d %B %Y · %I:%M %p IST}</b></div>',unsafe_allow_html=True)
with st.spinner("Loading latest risk news..."):news,status=fetch_news();market=markets()
if not news:st.error("No relevant stories received. Check VM internet/proxy access.");st.stop()
df=pd.DataFrame(news);df["published"]=pd.to_datetime(df["published"])
with st.sidebar:
    st.header("Filters")
    if st.button("🔄 Refresh News",use_container_width=True):st.cache_data.clear();st.rerun()
    st.caption(f'Feeds: {status["ok"]}/{status["ok"]+status["failed"]} · Moneycontrol: {status["moneycontrol"]} · Bank news removed: {status["banks_dropped"]}')
    q=st.text_input("Search",placeholder="company, state, risk...");hours=st.select_slider("Time window",[6,12,24,48],value=48,format_func=lambda x:f"Last {x}h")
    streams=st.multiselect("Stream",[STREAM_MF,STREAM_CLIMATE],default=[STREAM_MF,STREAM_CLIMATE]);sev=st.multiselect("Severity",["High","Medium","Low"],default=["High","Medium","Low"])
    sources=sorted(df.source.unique());selected_sources=st.multiselect("Source",sources,default=[])
    cats=sorted(df.category.unique());selected_cats=st.multiselect("Category",cats,default=cats);alls=sorted({s for x in df.states for s in x});selected_states=st.multiselect("State",alls)
f=df[(df.published>=pd.Timestamp(now-timedelta(hours=hours)))&df.stream.isin(streams)&df.severity.isin(sev)&df.category.isin(selected_cats)]
if selected_sources:f=f[f.source.isin(selected_sources)]
if selected_states:f=f[f.states.apply(lambda x:any(s in x for s in selected_states))]
if q:
    z=q.lower();f=f[(f.title+" "+f.source+" "+f.summary+" "+f.states.apply(" ".join)).str.lower().str.contains(z,regex=False)]
fm=f[f.stream==STREAM_MF];fc=f[f.stream==STREAM_CLIMATE];mc=f[f.source.str.lower()=="moneycontrol"]
k=st.columns(7);k[0].metric("MFI/JLG/NBFC",len(fm));k[1].metric("Moneycontrol",len(mc));k[2].metric("🌦 Climate",len(fc));k[3].metric("🔴 High Risk",int((f.severity=="High").sum()));k[4].metric("🟠 Medium",int((f.severity=="Medium").sum()));k[5].metric("States",len({s for x in f.states for s in x}));k[6].metric("Bank News Removed",status["banks_dropped"])
def card(r):
    cls="mc" if r.source.lower()=="moneycontrol" else ("clim" if r.stream==STREAM_CLIMATE else "");bs=f'<span class="badge {cls}">{html.escape(r.category)}</span><span class="badge">{r.severity}</span>';nums="".join(f'<span class="badge num">{html.escape(x)}</span>' for x in r.numbers);sts="".join(f'<span class="badge state">{html.escape(x)}</span>' for x in r.states);return f'<div class="card {r.severity}"><div class="meta">{bs}<b>{html.escape(r.source)}</b> · {r.published:%d-%b %I:%M %p}</div><a href="{html.escape(r.link)}" target="_blank">{html.escape(r.title)}</a><div style="margin-top:6px">{nums}{sts}</div></div>'
def cards(d):
    if d.empty:st.info("No stories match current filters.");return
    cols=st.columns(2)
    for i,r in enumerate(d.itertuples()):cols[i%2].markdown(card(r),unsafe_allow_html=True)
t1,t2,t3,t4,t5,t6=st.tabs(["🧑‍🤝‍🧑 MFI / JLG / NBFC","🟠 Moneycontrol","🌦 Climate Risk","⚠️ High Risk","📈 Market","⬇️ Export"])
with t1:cards(fm)
with t2:
    st.caption("Dedicated Moneycontrol NBFC/MFI coverage from sector terms and tracked companies.");cards(mc)
with t3:cards(fc)
with t4:cards(f[f.severity=="High"])
with t5:
    if market:st.dataframe(pd.DataFrame(market).style.format({"LTP":"{:,.2f}","Change":"{:+,.2f}","Change %":"{:+.2f}%"}),hide_index=True,use_container_width=True)
    else:st.warning("Market feed unavailable.")
with t6:
    out=f.copy();out["published_ist"]=out.published.dt.strftime("%Y-%m-%d %H:%M");out["states"]=out.states.apply(" | ".join);out["numbers"]=out.numbers.apply(" | ".join);cols=["published_ist","stream","severity","category","states","source","title","numbers","link"];st.dataframe(out[cols],hide_index=True,use_container_width=True,column_config={"link":st.column_config.LinkColumn("link",display_text="Open")});st.download_button("⬇️ Download Current View CSV",out[cols].to_csv(index=False).encode("utf-8-sig"),f"Risk_Daily_News_{now:%Y-%m-%d}.csv","text/csv")
st.caption("Bank-specific news is excluded. Moneycontrol coverage combines its business RSS with dedicated Moneycontrol site searches for NBFC/MFI terms and tracked companies. Risk tags are keyword-based; verify source before action.")
