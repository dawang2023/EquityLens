from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import json, math, re, os, unicodedata
ROOT=Path(__file__).resolve().parent
def num(v):
    try:
        v=float(v)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError): return None
def bn(v): return v/1e9 if v is not None else None
def ratio(a,b): return a/b*100 if a is not None and b is not None and b!=0 else None
ALIASES={'lvmh':'MC.PA','hermes':'RMS.PA','kering':'KER.PA','apple':'AAPL','microsoft':'MSFT','tesla':'TSLA','bnp paribas':'BNP.PA','allianz':'ALV.DE','basf':'BAS.DE','bp':'BP.L','moncler':'MONC.MI','totalenergies':'TTE.PA','nvidia':'NVDA','苹果':'AAPL','微软':'MSFT','爱马仕':'RMS.PA','英伟达':'NVDA'}
def search_company(q):
    key=''.join(c for c in unicodedata.normalize('NFKD',q.casefold()) if not unicodedata.combining(c)).strip()
    if key in ALIASES: return [{'symbol':ALIASES[key],'name':q,'exchange':'Primary listing'}]
    import yfinance as yf
    result=[]
    for v in yf.Search(q,max_results=12,news_count=0,lists_count=0,timeout=15).quotes:
        s=v.get('symbol','')
        if v.get('quoteType')=='EQUITY' and re.fullmatch(r'[A-Z0-9.\-^=]{1,24}',s):
            result.append({'symbol':s,'name':v.get('longname') or v.get('shortname') or s,'exchange':v.get('exchDisp') or v.get('exchange') or ''})
    return sorted(result,key=lambda v:v['symbol'].casefold()!=q.casefold())
def fetch(ticker):
    import yfinance as yf
    t=yf.Ticker(ticker)
    info=t.info; inc=t.income_stmt; bal=t.balance_sheet; cf=t.cashflow; hist=t.history(period='5d',auto_adjust=False,timeout=15)
    x={}; evidence={}
    def put(k,v,desc): x[k]=v; evidence[k]=desc if v is not None else 'Missing — verify and enter a value.'
    def row(frame,date,*keys):
        for k in keys:
            if date in frame.columns and k in frame.index:
                n=num(frame.loc[k,date])
                if n is not None:return n
        return None
    date=max(inc.columns) if len(inc.columns) else None
    ds='Annual statements '+str(date)
    close=num(hist['Close'].dropna().iloc[-1]); quote_date=str(hist['Close'].dropna().index[-1]); currency=info.get('currency'); fin=info.get('financialCurrency')
    if not currency or not fin: raise ValueError('Missing currency information.')
    if currency!=fin and not(currency=='GBp' and fin=='GBP'): raise ValueError('Quote and financial reporting currencies differ; select local listing.')
    if currency=='GBp': close/=100; currency='GBP'
    sector='financial' if info.get('sector')=='Financial Services' else 'industrial'
    put('company',f"{info.get('shortName') or ticker} ({ticker})",'Yahoo quote identity')
    put('sector',sector,'Yahoo sector, verify classification');put('currency',currency,'Yahoo quote and reporting currency');put('year',date.year if date is not None else None,ds)
    put('price',close,'Latest daily close '+quote_date);shares=num(info.get('sharesOutstanding'));put('shares',bn(shares),'Yahoo shares outstanding')
    rev=row(inc,date,'TotalRevenue','OperatingRevenue');ebit=row(inc,date,'OperatingIncome','EBIT');earn=row(inc,date,'NetIncomeCommonStockholders','NetIncome')
    debt=row(bal,date,'TotalDebt');cash=row(bal,date,'CashAndCashEquivalents');book=row(bal,date,'StockholdersEquity','CommonStockEquity');minority=row(bal,date,'MinorityInterest')
    da=row(cf,date,'DepreciationAndAmortization','DepreciationAmortizationDepletion');capex=row(cf,date,'CapitalExpenditure');tax=ratio(row(inc,date,'TaxProvision'),row(inc,date,'PretaxIncome'))
    put('revenue',bn(rev),ds+' revenue');put('ebit',ratio(ebit,rev),ds+' operating margin');put('da',ratio(da,rev),ds+' D&A / revenue');put('capex',ratio(abs(capex) if capex is not None else None,rev),ds+' capex / revenue')
    put('earnings',bn(earn),ds+' net income, flat forecast proxy');put('tax',tax if tax is not None and 0<=tax<=50 else 25,'Reported effective tax if plausible; otherwise 25% assumption')
    prior=sorted(inc.columns,reverse=True)[1] if len(inc.columns)>1 else None;prev=row(inc,prior,'TotalRevenue','OperatingRevenue')
    growth=ratio(rev-prev,prev) if rev is not None and prev is not None and prev>0 else None
    put('growth',max(-5,min(12,growth)) if growth is not None else 3,'Historical growth clipped to -5% to 12%; otherwise assumed 3%')
    put('grossDebt',bn(debt),ds+' debt');put('debt',bn(debt-cash) if debt is not None and cash is not None else None,ds+' debt minus cash');put('claims',bn(minority) if minority is not None else 0,'Reported minority interest or zero placeholder; verify other claims')
    put('book',book/shares if book is not None and shares and shares>0 else None,ds+' book equity / shares')
    put('beta',num(info.get('beta')),'Yahoo beta');div=num(info.get('trailingAnnualDividendRate'));put('dividend',div,'Yahoo trailing annual dividend per share')
    payout=div*shares/earn*100 if div is not None and shares and earn and earn>0 else None
    put('payout',min(100,max(0,payout)) if payout is not None else 40,'Trailing payout or illustrative 40%')
    interest=row(inc,date,'InterestExpense','InterestExpenseNonOperating');kd=ratio(abs(interest),debt) if interest is not None and debt else None
    put('kd',kd if kd is not None and 0<kd<=20 else 4.5,'Historical interest / debt or illustrative 4.5%')
    for k,v in [('rf',3),('erp',5),('wc',10),('terminal',2),('years',5),('multiple',15),('evEbit',12),('pb',1.5),('weight',70)]:put(k,v,'Illustrative editable assumption, not market consensus')
    if all(x[k] is not None for k in ('price','shares','grossDebt','beta')):
        E=x['price']*x['shares'];D=x['grossDebt'];ke=x['rf']+x['beta']*x['erp'];w=(E*ke+D*x['kd']*(1-x['tax']/100))/(E+D) if E+D>0 else None
        put('wacc',round(w,3) if w is not None else None,'Calculated from CAPM and above inputs')
    else:put('wacc',None,'Missing capital inputs')
    warnings=['Forecast inputs and selected multiples are illustrative, not consensus. Verify lease treatment, debt bridge, fiscal dates and currency. Prices may be delayed.']
    x['source']=f'Yahoo Finance {ticker}; FY {date}; close {quote_date}; fetched {datetime.now(timezone.utc).isoformat()}. '+warnings[0]
    return {'inputs':x,'evidence':evidence,'warnings':warnings}
class Handler(BaseHTTPRequestHandler):
    def send(self,status,body,ctype):
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def js(self,status,obj):self.send(status,json.dumps(obj,allow_nan=False).encode(),'application/json; charset=utf-8')
    def do_GET(self):
        p=urlparse(self.path)
        if p.path=='/':return self.send(200,(ROOT/'index.html').read_bytes(),'text/html; charset=utf-8')
        if p.path=='/health':return self.js(200,{'status':'ok'})
        if p.path=='/api/search':
            q=parse_qs(p.query).get('q',[''])[0].strip()
            if not q or len(q)>120:return self.js(400,{'error':'Enter a valid company name.'})
            try:return self.js(200,{'matches':search_company(q)})
            except Exception as e:return self.js(502,{'error':'Yahoo search unavailable: '+str(e)[:200]})
        if p.path=='/api/market':
            t=parse_qs(p.query).get('ticker',[''])[0].strip().upper()
            if not re.fullmatch(r'[A-Z0-9.\-^=]{1,24}',t):return self.js(400,{'error':'Invalid ticker.'})
            try:return self.js(200,fetch(t))
            except Exception as e:return self.js(502,{'error':'Yahoo market data unavailable: '+str(e)[:200]})
        return self.js(404,{'error':'Not found'})
if __name__=='__main__':
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),Handler).serve_forever()
