#coding:utf8
#
# Copyright © 2011 stamit@stamit.gr 
# To be distributed under the terms of the GNU General Public License version 3.
#
import sys
import os
import select
import time
import calendar
import threading
import re
import readline
import StringIO
import json
import urllib
import HTTPS
import socket
import OpenSSL
import Proxies
import pylab
import PythonMagick
from math import *

currency_format_default = (5,2)
currency_formats = {
	'usd':(5,2),
	'lr':(5,2),
	'btc':(4,8),
}
rate_format_default = (3,4)
rate_formats = {
	('usd','btc'):(2,5),
	('btc','usd'):(1,6),
	('lr','btc'):(2,5),
	('btc','lr'):(1,6),
}

def currency_format(cur):
	f = currency_formats.get(cur)
	if f <> None:
		return f
	return currency_format_default

def decimal_round(num,decimals):
	a = 10.0**decimals
	return int(num*a+0.5)/a

def dict_merge(*args):
	c = {}
	for a in args:
		if a<>None:
			for k,v in a.iteritems():
				c[k] = v
	return c

def dateutc(date):
	m=re.match('^(\d\d\d\d)-(\d\d)-(\d\d)([ T](\d\d)(:(\d\d)(:(\d\d)(\.\d*)?)?)?)?$',date)
	if not m: raise ValueError('invalid date format')

	sse = calendar.timegm((
		int(m.group(1)),
		int(m.group(2)),
		int(m.group(3)),
		int(m.group(5) or '0'),
		int(m.group(7) or '0'),
		int(m.group(9) or '0'),
	))+float('0'+(m.group(10) or ''))

	return sse

def utcdate(secs_since_epoch=None):
	if secs_since_epoch==None:
		secs_since_epoch = time.time()
	return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(secs_since_epoch))

def align_number(num,left_digits,right_digits):
	s = (('%.'+str(right_digits)+'f')%num).rstrip('0').rstrip('.')
	i = s.find('.')
	if i<0:
		i = len(s)
		s = s+' '
	lsp = left_digits-i
	rsp = right_digits-len(s)+i+1
	if lsp>0:
		s = lsp*' ' + s
	if rsp>0:
		s = s + rsp*' '
	return s

def format_currency(num,cur,ratefor=None,nosymbol=False):
	if ratefor<>None:
		rf = rate_formats.get((cur,ratefor))
		if rf==None: rf=rate_format_default
		left,right = rf
	else:
		cf = currency_formats.get(cur)
		if cf==None: cf=currency_format_default
		left,right = cf

	s = align_number(num,left,right)
	if nosymbol:
		return s
	else:
		return s+' '+cur.upper()

def ifnull(*args):
	for x in args:
		if x<>None:
			return x
	return None

def recv_line(sock):
	'''removes \n or \r\n at end of line'''
	s = StringIO.StringIO()
	while True:
		c = sock.recv(1)
		if c=='\n': break
		s.write(c)
	x = s.getvalue()
	if len(x) and x[-1]=='\r':
		x = x[:-1]
	return x

def read_config(filename, stripem=1):
	sections = {}
	cursection = {}
	sections[None]=cursection

	f = open(filename,'r')
	lineno = 0
	while True:
		line = f.readline()
		if line=='': break
		line = line.rstrip('\r\n')

		lineno += 1

		if (len(line)>=1 and line[0]=='#') or line.strip()=='':
			continue

		m = re.match('^\[(.*)\]$', line)
		if m<>None:
			if sections.get(m.group(1))<>None:
				raise Exception('%s:%d: duplicate section'%(filename,lineno))
			cursection = {}
			sections[m.group(1)] = cursection
			continue

		nv = line.split('=',1)
		if len(nv)==1:
			if stripem:
				nv[0] = nv[0].strip()
			cursection[nv[0]] = ''
		elif len(nv)==2:
			if stripem:
				nv[0] = nv[0].strip()
				nv[1] = nv[1].strip()
			cursection[nv[0]] = nv[1]
		else:
			raise Exception('%s:%d: invalid config file syntax'%(filename,lineno))
	f.close()

	return sections

def depth_data_process(depth, count=None, vol=None):
	bids,asks = depth_data_normalize(depth)
	return depth_data_accum(bids,asks, count, vol)

def depth_data_normalize(depth):
	bids,asks = depth['bids'],depth['asks']

	for i in range(len(bids)):
		price,amount = bids[i]
		bids[i] = (float(price),float(amount))
	for i in range(len(asks)):
		price,amount = asks[i]
		asks[i] = (float(price),float(amount))

	bids.sort()
	asks.sort()

	return bids,asks

def depth_data_accum(bids,asks, count=None, vol=None):
	bcount = acount = count

	if bcount==None:
		bcount = len(bids)
	else:
		bcount = min(bcount,len(bids))
	if acount==None:
		acount = len(asks)
	else:
		acount = min(acount,len(asks))

	sum = 0.0
	csum = 0.0
	for i in range(0,bcount):
		sum += bids[-i-1][1]
		csum += bids[-i-1][0]*bids[-i-1][1]
		bids[-i-1] = bids[-i-1] + (sum,csum)
		if vol<>None and sum>=vol:
			bcount = i+1
			break

	sum = 0.0
	csum = 0.0
	for i in range(0,acount):
		sum += asks[i][1]
		csum += asks[i][0]*asks[i][1]
		asks[i] = asks[i] + (sum,csum)
		if vol<>None and sum>=vol:
			acount = i+1
			break

	return bids[-bcount:],asks[:acount]

class Exchanger:
	currencies={}

	def __init__(self, cfg, name):
		self.config = dict_merge(
			cfg.get(None),
			cfg.get(name)
		)
		self.name = name
		self.defasset = 'btc'
		self.defcurrency = 'usd'
		if self.config.get('default')<>None:
			a,c = self.config.get('default').split()
			self.setdefault(a.lower(),c.lower())

	def _q(self, path, postdata=None):
		if self.config.get('digest')==None:
			raise Exception('you must provide an SSL certificate digest')

		headers,v = HTTPS.do_https_query(
			'https://%s:%d/%s'%(
				self.config['host'],
				int(ifnull(self.config['port'],443)),
				path.lstrip('/')
			),
			postdata,
			digest=self.config['digest'],
			digesttype=(self.config.get('digest.type') or 'sha1'),
			proxy=(self.config.get('proxy.host'),int(self.config.get('proxy.port') or 0)),
			proxytype=self.config.get('proxy.type'),
		)
		return v

	def getdefault(self, a=None, c=None):
		if a==None and c==self.defasset:
			a = self.defcurrency
		elif a==self.defcurrency and c==None:
			c = self.defasset
		else:
			a,c = (ifnull(a,self.defasset),ifnull(c,self.defcurrency))
		if a==c:
			raise ValueError('asset and currency cannot be same')
		if a not in self.currencies:
			raise ValueError('unsupported asset (%s)'%str(a))
		if c not in self.currencies:
			raise ValueError('unsupported currency (%s)'%str(c))
		return a,c
	def setdefault(self, asset, currency):
		self.defasset,self.defcurrency = self.getdefault(asset,currency)

	def ticker(self, asset=None, currency=None):
		raise Exception('not implemented')

	def _reverse_getdepth(self,count,vol,asset,currency):
		bids,asks = self.getdepth(count,vol,currency,asset)
		for i in range(len(bids)):
			price,amount = bids[i][:2]
			bids[i] = (1.0/price,price*amount)+(bids[i][3],bids[i][2])
		bids.reverse()
		for i in range(len(asks)):
			price,amount = asks[i][:2]
			asks[i] = (1.0/price,price*amount)+(asks[i][3],asks[i][2])
		asks.reverse()
		return asks,bids
	def getdepth(self, count=None, vol=None, asset=None, currency=None):
		raise Exception('not implemented')

	def _reverse_trades(self,count,date,asset,currency):
		ts = self.trades(count,date,currency,asset)
		for i in range(len(ts)):
			ts[i]['amount'] = ts[i]['amount']*ts[i]['price']
			ts[i]['price'] = 1.0/ts[i]['price']
		return ts
	def trades(self, count=None, asset=None, currency=None):
		raise Exception('not implemented')

	def getfunds(self, asset=None, currency=None):
		raise Exception('not implemented')
	def buy(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def buyfees(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def sell(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def sellfees(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def getorders(self, asset=None, currency=None):
		raise Exception('not implemented')
	def cancel(self, oid, asset=None, currency=None):
		raise Exception('not implemented')

	def selectfds(self):
		return ([],[],[])
	def onselect(self,fds):
		pass
	def close(self):
		pass

def mtgox_websocket_connect(s, proxytype=None,proxy=None):
	host='websocket.mtgox.com'
	port=80

	if proxytype=='http':
		s.connect(proxy)
		Proxies.do_http_connect(s,host,port)
	elif proxytype=='socks4a':
		s.connect(proxy)
		Proxies.do_socks4a(s,host,port)
	else:
		s.connect((host,port))

	querylines = [
		'GET /mtgox HTTP/1.1',
		'Upgrade: WebSocket',
		'Connection: Upgrade',
		'Host: websocket.mtgox.com',
		'Origin: null',
	]
	s.send('\r\n'.join(querylines)+'\r\n\r\n')

	lines = []
	while True:
		line = recv_line(s)
		if line=='': break
		lines.append(line)

	if lines[0]<>'HTTP/1.1 101 Web Socket Protocol Handshake':
		raise Exception('server did not say "101" (said %s)'%repr(lines[0]))

	return s

class MtGoxExchanger(Exchanger):
	currencies={
		'usd':True,
		'btc':True,
	}

	def __init__(self, cfg, name):
		Exchanger.__init__(self,cfg,name)
		if self.config.get('host')==None:
			self.config['host'] = 'data.mtgox.com'
		self.plainsocket = None
		self.sslsocket = None
		self.ssltimeout = None
		self.wsthread = None
		self.websocket = None
		self.wsmarkets = {}
		self.wssync = None
		if self.config.get('websocket')<>None:
			self.wsthread = threading.Thread(target=self._ws_run)
			self.wsthread.start()

	def _q(self, path, postdata=None):
		if self.config.get('digest')==None:
			raise Exception('you must provide an SSL certificate digest')

		headers,v = self._ssl_query(path, postdata)
		return v

	def _ssl_connect(self):
		self.sslhost = ifnull(self.config.get('host'), 'mtgox.com')
		port = int(ifnull(self.config.get('port'), 443))

		self.plainsocket = s = socket.socket()
		if self.config.get('proxy.type')<>None:
			s.connect((
				self.config.get('proxy.host'),
				int(self.config.get('proxy.port'))
			))
			do_proxy_connect(s, self.config.get('proxy.type'), self.sslhost, port)
		else:
			s.connect((self.sslhost,port))

		ctx = OpenSSL.SSL.Context(OpenSSL.SSL.TLSv1_METHOD)
		self.sslsocket = c = OpenSSL.SSL.Connection(ctx,s)
		c.set_connect_state()
		c.do_handshake()

		digest = self.config['digest']
		digesttype = self.config.get('digest.type') or 'sha1'
		if digest==None or digesttype==None:
			raise Exception('refusing to connect without checking certificate digest')

		cert = c.get_peer_certificate()
		digestgot = cert.digest(digesttype)
		if digestgot<>digest:
			raise Exception('server certificate mismatch (got %s)'%digestgot)
	def _ssl_close(self):
		if self.sslsocket<>None:
			self.sslsocket.close()
			self.sslsocket = None
		if self.plainsocket<>None:
			self.plainsocket.close()
			self.plainsocket = None
	def _ssl_query(self, path, postdata=None, headers=None):
		t = time.time()

		if self.ssltimeout<>None and self.ssltimeout<t:
			self._ssl_close()
		if self.sslsocket==None:
			self._ssl_connect()

		if headers==None:
			headers=[]
		headers.append('Connection: Keep-Alive')
		rh,v = HTTPS.do_http_query(self.sslsocket, self.sslhost, path, postdata, headers=headers)

		closeit = True
		kaparams = {}
		for h in rh:
			if h=='Connection: Keep-Alive':
				closeit = False
			elif h.startswith('Keep-Alive:'):
				x,y = h.split(':',1)
				kaps = y.strip().split(',')
				for kaparam in kaps:
					x,y = kaparam.split('=',1)
					kaparams[x.strip()] = y.strip()
		if closeit:
			self._ssl_close()
			self.ssltimeout = None
		elif kaparams.get('timeout'):
			self.ssltimeout = t+float(kaparams['timeout'])
		return rh,v

	def _ws_connect(self):
		ws = socket.socket()
		mtgox_websocket_connect(ws,
			ifnull(
				self.config.get('websocket.proxy.type'),
				self.config.get('proxy.type')
			),
			(
				ifnull(
					self.config.get('websocket.proxy.host'),
					self.config.get('proxy.host')
				),
				int(ifnull(
					self.config.get('websocket.proxy.port'),
					self.config.get('proxy.port'),
					0
				)),
			)
		)
		self.websocket = ws
	def _ws_run(self):
		if self.websocket==None:
			de = float(self.config.get('websocket.initdelay') or 5)
			for i in range(int(de*10)):
				time.sleep(0.1)
				if self.wsthread==None:
					break

		while self.wsthread<>None:
			if self.websocket==None:
				self._ws_connect()

			wsfd = self.websocket.fileno()
			r,w,x = select.select([wsfd],[],[],float(self.config.get('websocket.select.timeout') or 1))
			for fd in r:
				if fd==wsfd:
					frame = self._ws_recv()
					if not frame and self.websocket<>None:
						self.websocket.close()
						self.websocket=None
						sys.stderr.write('WEBSOCKET CLOSED - WILL RECONNECT\n')
						self._ws_connect()
					else:
						self._ws_onframe(frame)

		if self.websocket<>None:
			self.websocket.close()
			self.websocket = None
	def _ws_send(self, js):
		self.websocket.send(chr(0x00)+json.dumps(js)+chr(0xFF))
	def _ws_recv(self):
		c = self.websocket.recv(1)
		if c=='':
			return None
		if c<>chr(0x00):
			raise Exception('expecting frames to begin with NUL byte; got %s'%repr(c))

		s = StringIO.StringIO()
		while True:
			c = self.websocket.recv(1)
			if c==chr(0xFF): break
			s.write(c)
		return json.loads(s.getvalue())
	def _ws_market(self, ass, cur, elem, default=None):
		m = self.wsmarkets.get((ass,cur))
		if m==None:
			m = {}
			self.wsmarkets[(ass,cur)] = m
		value = m.get(elem)
		if value==None and default<>None:
			value = default()
			m[elem] = value
		return value
	def _ws_market_set(self, ass, cur, elem, value):
		m = self.wsmarkets.get((ass,cur))
		if m==None:
			m = {}
			self.wsmarkets[(ass,cur)] = m
		m[elem] =  value
	def _ws_onframe(self, x):
		if x==None:
			x = s.recv_frame()
			if x==None: return True

		if x['op']=='unsubscribe':
			self._ws_log('UNSUBSCRIBE', x['channel'])

		elif x['op']=='subscribe':
			self._ws_log('SUBSCRIBE', x['channel'])

		elif x['op']<>'private':
			self._ws_log('OTHER', x)

		elif x['private']=='ticker':
			self._ws_log('TICKER', '%g'%x['ticker']['buy'], '%g'%x['ticker']['sell'])

			self._ws_market_set('btc','usd','ticker',{
				'buy':float(x['ticker']['buy']),
				'sell':float(x['ticker']['sell']),
			})

		elif x['private']=='trade':
			y = x['trade']

			date = time.time()
			asset = y['item'].lower()
			currency = y['price_currency'].lower()
			amount = float(y['amount'])
			price = float(y['price'])
			a = format_currency(amount, asset)
			p = format_currency(price, currency,asset)

			if y['trade_type']=='ask':
				t = 'SELL'
			else:
				t = 'BUYX'
			if t=='SELL':
				self._ws_log("%s %s at %s"%(t, a, p), esc='\x1b[1m\x1b[31m')
			elif t=='BUYX':
				self._ws_log("%s %s at %s"%(t, a, p), esc='\x1b[1m\x1b[32m')
			else:
				self._ws_log("%s %s at %s"%('?'+t, a, p))

			self._ws_market(asset,currency,'trades', default=list).append({
				'date':date,
				'price':price,
				'amount':amount,
			})

		elif x['private']=='depth':
			y = x['depth']
			asset = y['item'].lower()
			currency = y['currency'].lower()
			amount = float(y['volume'])
			price = float(y['price'])
			a = format_currency(amount, asset)
			p = format_currency(price, currency,asset)

			if self.config.get('websocket.depth.dontsync'):
				default=dict
			else:
				default=None

			t = y['type_str'].upper()
			if t=='BID':
				wsbids = self._ws_market(asset,currency,'bids',default=default)
				if wsbids<>None:
					wsbids[price] = decimal_round(
						(wsbids.get(price) or 0.0) + amount,
						currency_format(asset)[1]
					)
					if wsbids[price]<=0.0:
						del wsbids[price]
			elif t=='ASK':
				wsasks = self._ws_market(asset,currency,'asks',default=default)
				if wsasks<>None:
					wsasks[price] = decimal_round(
						(wsasks.get(price) or 0.0) + amount,
						currency_format(asset)[1]
					)
					if wsasks[price]<=0.0:
						del wsasks[price]

			self._ws_log("%s %s at %s"%(t,a,p))
		else:
			self._ws_log('PRIVATE', x)
	def _ws_log(self, *args, **kwargs):
		pass
	def _ws_bidask(self,ass,cur, wsbids, wsasks):
		bids = []
		for price,amount in wsbids.items():
			bids.append((price,amount))
		bids.sort()
		asks = []
		for price,amount in wsasks.items():
			asks.append((price,amount))
		asks.sort()
		return bids,asks
	def _ws_bidask_set(self,ass,cur,bids,asks):
		wsbids = self._ws_market(ass,cur,'bids', default=dict)
		for bid in bids:
			wsbids[bid[0]] = bid[1]
		wsasks = self._ws_market(ass,cur,'asks', default=dict)
		for ask in asks:
			wsasks[ask[0]] = ask[1]

	def _jsq(self, path, postdata=None):
		txt = self._q(path,postdata)
		try:
			v = json.loads(txt)
		except Exception, x:
			raise Exception('invalid JSON reply (%s)'%txt)
		if type(v)==dict and v.has_key('error'):
			raise Exception(v['error'])
		return v

	def _up(self):
		up = []

		username = self.config.get('username')
		if username<>None: up.append('name=%s'%urllib.quote(username))
		password = self.config.get('password')
		if password<>None: up.append('pass=%s'%urllib.quote(password))
		apitoken = self.config.get('apitoken')
		if apitoken<>None: up.append('token=%s'%urllib.quote(apitoken))

		up = '&'.join(up)
		return up

	def _lastoid(self, status, t, amount, price, type):
		# get the most recent "in queue" order that matches price
		mind = None
		order = None
		for o in status['orders']:
			d = abs(float(o['date'])-t)
			pd = abs(float(o['price'])-price)
			ad = amount-float(o['amount'])
			if mind==None or mind<d:
				if pd<0.0001 and ad<0.001 and o['type']==type:
					mind = d
					order = o
		if order<>None:
			return (order['oid'],order['type'])
		else:
			return None

	def _path(self, op, currency):
		return {
			'ticker':'/code/data/ticker.php',
			'depth':'/code/data/getDepth.php',
			'trades':'/code/data/getTrades.php',
			'funds':'/code/getFunds.php',
			'orders':'/code/getOrders.php',
			'cancel':'/code/cancelOrder.php',
			'buy':'/code/buyBTC.php',
			'sell':'/code/sellBTC.php',
		}[op]

	def ticker(self, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			ticker = self.ticker('btc',asset)
			return {
				'buy':1.0/ticker['sell'],
				'sell':1.0/ticker['buy'],
			}

		wsticker = self._ws_market(asset,currency,'ticker')
		if wsticker<>None:
			return wsticker

		ticker = self._jsq(self._path('ticker',currency))['ticker']
		return {
			'buy':float(ticker['buy']),
			'sell':float(ticker['sell']),
		}

	def getdepth(self, count=None, vol=None, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			return self._reverse_getdepth(count,vol,asset,currency)

		usews = True
		t = time.time()
		resync = self.config.get('websocket.depth.resync')
		wssync = self._ws_market(asset,currency,'sync')
		if wssync<>None and resync<>None and (wssync+float(resync)) < t:
			usews = False

		wsbids = self._ws_market(asset,currency,'bids')
		wsasks = self._ws_market(asset,currency,'asks')
		if wsbids<>None and wsasks<>None and usews:
			bids,asks = self._ws_bidask(asset,currency, wsbids, wsasks)
		else:
			bids,asks = depth_data_normalize(self._jsq(self._path('depth',currency)))
			if self.websocket<>None:
				self._ws_bidask_set(asset,currency,bids,asks)
				self._ws_market_set(asset,currency,'sync',t)

		return depth_data_accum(bids,asks, count, vol)

	def trades(self, count=None, date=None, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			return self._reverse_trades(count,date,asset,currency)

		wstrades = self._ws_market(asset,currency,'trades', default=list)
		usews = False
		if self.websocket<>None:
			if self.config.get('websocket.tradesalways'):
				usews = True
			if count<>None and len(wstrades)>=count:
				usews = True
			if date<>None and len(wstrades)>0 and wstrades[0]['date']<date:
				usews = True

		if usews:
			ts = wstrades
			if date<>None:
				for i in range(len(ts)):
					if ts[i]['date']>=date:
						ts = ts[i:]
						break
		else:
			# date tid price amount
			ts = []
			for trade in self._jsq(self._path('trades',currency)):
				tdate = float(trade['date'])
				if date<>None and tdate<date:
					continue
				ts.append({
					'date':tdate,
					'amount':float(trade['amount']),
					'price':float(trade['price']),
				})

		if count<>None:
			if count==0:
				return []
			else:
				return ts[-count:]
		else:
			return ts

	def getfunds(self, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		funds = self._jsq(self._path('funds',self.defcurrency), self._up())
		return {
			asset:float(funds[asset+'s']),
			currency:float(funds[currency+'s']),
		}

	def buy(self, amount, price, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			btc_amount = amount*price
			return self.sell(btc_amount, 1.0/price, 'btc', asset)

		if amount<=0:
			raise ValueError('"amount" must be positive')
		if price<=0:
			raise ValueError('"price" must be positive')

		if self.config.get('safety')<>None:
			raise Exception('safety is on')

		status = self._jsq(self._path('buy',currency), self._up()+'&amount=%.8f&price=%g'%(
			float(amount),
			float(price)
		))
		t = time.time()
		oid = self._lastoid(status,t,amount,price,2)
		if oid<>None:
			return '/'.join(map(str,oid))
		else:
			return None

	def buyfees(self, amount, price, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			btc_amount = amount*price
			return self.buyfees(btc_amount, 1.0/price, 'btc', asset)

		if amount<=0:
			raise ValueError('"amount" must be positive')
		if price<=0:
			raise ValueError('"price" must be positive')

		feerate = float(ifnull(
			self.config.get('fee.'+currency+'.'+asset),
			self.config.get('fee'),
			0.3
		))/100
		feecurrency = ifnull(
			self.config.get('fee.'+currency+'.'+asset+'.currency'),
			asset
		)
		if feecurrency==asset:
			feeamount = amount*feerate
		elif feecurrency==currency:
			feeamount = amount*price*feerate
		else:
			raise Exception('fee.%s.%s.currency must be either %s or %s'%(currency,asset,currency,asset))

		return { feecurrency:feeamount }

	def sell(self, amount, price, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			btc_amount = amount*price
			return self.buy(btc_amount, 1.0/price, 'btc', asset)

		if amount<=0:
			raise ValueError('"amount" must be positive')
		if price<=0:
			raise ValueError('"price" must be positive')

		if self.config.get('safety')<>None:
			raise Exception('safety is on')

		status = self._jsq(self._path('sell',currency), self._up()+'&amount=%.8f&price=%g'%(
			float(amount),
			float(price)
		))
		t = time.time()
		oid = self._lastoid(status,t,amount,price,1)
		if oid<>None:
			return '/'.join(map(str,oid))
		else:
			return None

	def sellfees(self, amount, price, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			btc_amount = amount*price
			return self.buyfees(btc_amount, 1.0/price, 'btc', asset)

		if amount<=0:
			raise ValueError('"amount" must be positive')
		if price<=0:
			raise ValueError('"price" must be positive')

		feerate = float(ifnull(
			self.config.get('fee.'+asset+'.'+currency),
			self.config.get('fee'),
			0.03
		))/100
		feecurrency = ifnull(
			self.config.get('fee.'+asset+'.'+currency+'.currency'),
			self.config.get('fee.currency'),
			currency
		)
		if feecurrency==asset:
			feeamount = amount*feerate
		elif feecurrency==currency:
			feeamount = amount*price*feerate
		else:
			raise Exception('fee.%s.%s.currency must be either %s or %s'%(currency,asset,currency,asset))

		return { feecurrency:feeamount }

	def getorders(self, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)

		if currency=='btc':
			orders = self.getorders('btc',asset)
			for i in range(len(orders)):
				orders[i]['type'] = 3-orders[i]['type']
				orders[i]['amount'] = \
					orders[i]['amount']*orders[i]['price']
				orders[i]['price'] = 1.0/orders[i]['price']
			return orders

		os = self._jsq(self._path('orders',currency), self._up())['orders']
		orders = []
		for o in os:
			oasset=ifnull(o.get('item'),o.get('symbol')).lower()
			ocurrency=ifnull(o.get('currency'),o.get('reserved_currency')).lower()
			otype = int(o['type'])
			if not otype in [1,2]:
				sys.stderr.write('unrecognized order type (%s)'%o['type'])
				continue
			if o['status']<>1:
				otype = -otype

			if oasset==asset and ocurrency==currency:
				orders.append({
					'id':str(o['oid'])+'/'+str(o['type']),
					'type':otype,
					'amount':float(o['amount']),
					'price':float(o['price']),
				})
			elif oasset==currency and ocurrency==asset:
				otype = 3-otype
				orders.append({
					'id':str(o['oid'])+'/'+str(o['type']),
					'type':ot,
					'amount':float(o['price'])*float(o['amount']),
					'price':1.0/float(o['price']),
				})
			elif oasset==asset and ocurrency==asset and self.config.get('class')=='TradeHill':
				orders.append({
					'id':str(o['oid'])+'/'+str(o['type']),
					'type':abs(otype),
					'amount':float(o['amount']),
					'price':float(o['price']),
				})
		orders.sort(cmp=lambda a,b:cmp(a['price'],b['price']))
		return orders

	def cancel(self, oid, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if currency=='btc':
			return self.cancel(oid, 'btc',asset)

		oid,t = oid.split('/')
		self._jsq(self._path('cancel',currency), self._up()+'&oid=%s&type=%s'%(
			urllib.quote(str(oid)),
			urllib.quote(str(t)),
		))

	def selectfds(self):
		if self.websocket<>None:
			return ([self.websocket.fileno()],[],[])
	def onselect(self,(r,w,x)):
		ret = False
		for fd in r:
			if self.websocket<>None and fd==self.websocket.fileno():
				frame = self._ws_recv()
				if frame<>None:
					self.websocket._ws_onframe(frame)
					ret = True
		return ret
	def close(self):
		if self.wsthread<>None:
			self.wsthread = None
		self._ssl_close()

class TradeHillExchanger(MtGoxExchanger):
	currencies={
		'usd':True,
		'lr':True,
		'btc':True,
		'ars':True,
		'aud':True,
		'brl':True,
		'cad':True,
		'chf':True,
		'clp':True,
		'cny':True,
		'czk':True,
		'dkk':True,
		'eur':True,
		'gbp':True,
		'hkd':True,
		'ils':True,
		'inr':True,
		'jpy':True,
		'mxn':True,
		'nzd':True,
		'nok':True,
		'pen':True,
		'pln':True,
		'sgd':True,
		'zar':True,
		'sek':True,
	}

	def __init__(self, cfg, name):
		MtGoxExchanger.__init__(self,cfg,name)

	def _path(self,op,currency):
		return ({
			'ticker':'/APIv1/%s/Ticker',
			'trades':'/APIv1/%s/Trades',
			'depth':'/APIv1/%s/Orderbook',
			'funds':'/APIv1/%s/GetBalance',
			'orders':'/APIv1/%s/GetOrders',
			'buy':'/APIv1/%s/BuyBTC',
			'sell':'/APIv1/%s/SellBTC',
			'cancel':'/APIv1/%s/CancelOrder',
		}[op])%currency.upper()

	def getdefault(self,a=None,c=None):
		a,c = MtGoxExchanger.getdefault(self,a,c)
		if a<>'btc' and c<>'btc':
			raise Exception('only BTC exchanges supported')
		return a,c

	def getfunds(self, asset=None, cur=None):
		asset,cur = self.getdefault(asset,cur)
		if cur=='btc':
			asset,cur=cur,asset

		curu = cur.upper()
		funds = self._jsq('/APIv1/%s/GetBalance'%curu, self._up())
		return {
			'btc':float(funds['BTC_Available'])+float(funds['BTC_Reserved']),
			cur:float(funds[curu+'_Available'])+float(funds[curu+'_Reserved']),
		}

class BtcexExchanger(Exchanger):
	def __init__(self, cfg, name):
		Exchanger.__init__(self,cfg,name)

	def ticker(self, asset=None, currency=None):
		bids,asks = self.getdepth(asset,currency)
		x={}
		if len(bids):
			x['buy'] = bids[-1][0]
		else:
			x['buy'] = None
		if len(asks):
			x['sell'] = asks[0][0]
		else:
			x['sell'] = None
		return x

	def getdepth(self, count=None, vol=None, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if asset=='usd' and currency=='btc':
			return self._reverse_getdepth(count,vol,asset,currency)
		if asset<>'btc' or currency<>'usd':
			raise Exception('only USD/BTC exchanges supported')

		v = self._q('/site/orders/2')
		asks=[]
		bids=[]
		for line in v.split('\n'):
			sl = line.rstrip('\r').split(',')
			if len(sl)<>3:
				raise Exception('unexpected order book format (%d fields)'%len(sl))
			o = (float(sl[1]),float(sl[2]))
			if sl[0].lower()=='ask':
				asks.append(o)
			elif sl[0].lower()=='bid':
				bids.append(o)
			else:
				raise Exception('unexpected order book format (2)')
		bids.sort()
		asks.sort()

		return depth_data_accum(bids,asks,count,vol)

	def trades(self, count=None, date=None, asset=None, currency=None):
		asset,currency = self.getdefault(asset,currency)
		if asset=='usd' and currency=='btc':
			return self._reverse_trades(count,date,asset,currency)
		if asset<>'btc' or currency<>'usd':
			raise Exception('only USD/BTC exchanges supported')

		v = self._q('/site/deals/2')
		ts=[]
		for line in v.split('\n'):
			if not line:
				continue

			sl = line.rstrip('\r').split(',')
			if len(sl)<>3:
				raise Exception('unexpected trades list format (%d fields)'%len(sl))

			tdate = dateutc(sl[0])
			if date<>None and tdate<date:
				continue
			ts.append({
				'date':tdate,
				'amount':float(sl[1]),
				'price':float(sl[2]),
			})
		ts.sort(cmp=lambda a,b:cmp(a['date'],b['date']))
		if count<>None:
			return ts[-count:]
		else:
			return ts

	def getfunds(self):
		#self._q('/api/funds?username=%s&token=%s'%(
		#	urllib.quote(self.config['username']),
		#	urllib.quote(self.config['token']),
		#))
		raise Exception('not implemented')

	def buy(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def buyfees(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def sell(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def sellfees(self, amount, price, asset=None, currency=None):
		raise Exception('not implemented')
	def getorders(self, asset=None, currency=None):
		raise Exception('not implemented')
	def cancel(self, oid, asset=None, currency=None):
		raise Exception('not implemented')
