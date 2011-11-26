import sys
import os
import socket
import select
import re
import time
import OpenSSL

DEBUG=0

class Transport:
	def __init__(self):
		pass
	def __fini__(self):
		self.close()
	def connect(self, address):
		pass
	def close(self):
		pass
	def read(self, n):
		return ''
	def recv(self, n):
		return self.read(n)
	def write(self, string):
		pass
	def send(self, string):
		return self.write(string)
	def fileno(self):
		''' input file descriptor for giving to select() '''
		''' DO NOT USE DIRECTLY '''
		raise Exception('Transport has no file descriptor')

class StdioTransport(Transport):
	def __init__(self, i=0, o=1):
		Transport.__init__(self)
		self.i=i
		self.o=o
	def connect(self, address):
		return self
	def close(self):
		pass
	def read(self, n):
		return os.read(self.i, n)
	def write(self, string):
		os.write(self.o, string)
	def fileno(self):
		return self.i

class ProcessTransport(StdioTransport):
	def __init__(self, command):
		send = os.pipe()
		recv = os.pipe()
		StdioTransport.__init__(self, recv[0], send[1])
		if os.fork()==0:
			os.close(0)
			os.close(1)
			os.dup2(send[0],0)
			os.dup2(recv[1],1)
			os.close(send[0])
			os.close(send[1])
			os.close(recv[0])
			os.close(recv[1])
			os.execvp('sh', ['sh','-c',command])
			sys.exit(255)
		else:
			os.close(send[0])
			os.close(recv[1])
	def __fini__(self):
		os.close(self.i)
		os.close(self.o)
		StdioTransport.__fini__(self)

class SocketTransport(Transport):
	def __init__(self):
		Transport.__init__(self)
		self.sock = None
		self.sock1 = None
	def listen(self, address):
		self.sock1 = socket.socket()
		self.sock1.bind(address)
		self.sock1.listen(1)
		self.sock,self.addrinfo = self.sock1.accept()
		return self
	def connect(self, address):
		self.sock = socket.socket()
		self.sock.connect(address)
		return self
	def close(self):
		if self.sock<>None:
			self.sock.close()
		if self.sock1<>None:
			self.sock1.close()
	def read(self, n):
		return self.sock.recv(n)
	def write(self, string):
		self.sock.send(string)
	def fileno(self):
		return self.sock.fileno()

class ParentedTransport(Transport):
	def __init__(self, parent):
		Transport.__init__(self)
		self.parent = parent
	def connect(self, endpoint):
		return parent.connect(endpoint)
	def close(self):
		return self.parent.close()
	def read(self, n):
		return self.parent.read(n)
	def write(self, string):
		return self.parent.write(string)
	def fileno(self):
		return self.parent.fileno()

class SOCKS4ATransport(ParentedTransport):
	def __init__(self, parent):
		ParentedTransport.__init__(self, parent)
	def connect(self, (host,port)):
		do_socks4a(self, host, port)
		return self

class HTTPTransport(ParentedTransport):
	def __init__(self, parent):
		ParentedTransport.__init__(self, parent)
	def connect(self, (host,port)):
		do_http_connect(self, host, port)
		return self

class SSLTransport(ParentedTransport):
	def __init__(self, parent, ctx=None):
		ParentedTransport.__init__(self, parent)
		self.parent = parent
		if ctx==None:
			ctx = OpenSSL.SSL.Context(OpenSSL.SSL.TLSv1_METHOD)
		self.ctx = ctx
		self.con = OpenSSL.SSL.Connection(ctx,parent)
	def connect(self, endpoint):
		'''endpoint is ignored'''
		#self.parent.connect(endpoint)
		self.con.set_connect_state()
		self.con.do_handshake()
		cert = self.con.get_peer_certificate()
		sys.stderr.write('certificate SHA1: %s\n'%cert.digest('sha1'))
		return self
	def close(self):
		sefl.con.close()
		#self.parent.close()
	def read(self, n):
		try:
			return self.con.read(n)
		except OpenSSL.SSL.ZeroReturnError, x:
			return ''
	def write(self, string):
		try:
			return self.con.write(string)
		except OpenSSL.SSL.ZeroReturnError, x:
			return ''

def do_socks4a(s, hostname, port):
	tosend = (
		chr(0x04)
		+ chr(0x01)
		+ chr(port>>8)+chr(port&0xFF)
		+ '\x00\x00\x00\x01'
		+ 'anonymous\x00'
		+ hostname + '\x00'
	)

	m = re.match('(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$',hostname)
	if m:
		ip = (
			int(m.group(1)),
			int(m.group(2)),
			int(m.group(3)),
			int(m.group(4)),
		)
		if ip[0]<256 and ip[1]<256 and ip[2]<256 and ip[3]<256:
			tosend = (
				chr(0x04)
				+ chr(0x01)
				+ chr(port>>8)+chr(port&0xFF)
				+ ''.join(map(chr,ip))
				+ 'anonymous\x00'
			)

	s.send(tosend)
	r = s.recv(8)
	if r=='':
		raise Exception('EOF or interrupt connecting via SOCKS4A to %s:%d'%(hostname,port))
	if r[1]<>'\x5a':
		raise Exception('SOCKS4A server cannot connect to %s port %d (%s)'%(hostname,port,repr(r)))
	if DEBUG:
		sys.stderr.write('SOCKS4A connected %s\n'%repr(r))

def do_http_connect(s, hostname, port):
	s.send('CONNECT %s:%d HTTP/1.1\r\n\r\n'%(hostname,port))
	r=''
	fl=None
	f=0
	while True:
		c = s.recv(1)
		if c=='':
			raise Exception('EOF or interrupt connecting via HTTP to %s:%d'%(hostname,port))
		r = r+c
		if f==0:
			if c=='\n':
				if fl==None:
					fl = r
				f=1
		elif f==1:
			if c=='\n':
				break
			elif c<>'\r':
				f=0

	#if not re.match('HTTP/1\.[01] 200 ([Cc]onnection [Ee]stablished|OK)', fl):
	if not re.match('HTTP/1\.[01] 200 ', fl):
		raise Exception('proxy did not say "200"; connecting via HTTP to %s:%d (%s)'%(hostname,port,repr(fl)))
	if DEBUG:
		sys.stderr.write('HTTP connected %s\n'%repr(r))

def do_proxy_connect(s, proxytype, host, port):
	if proxytype=='socks4a':
		do_socks4a(s,host,port)
	elif proxytype=='http':
		do_http_connect(s,host,port)
	else:
		raise Exception('unknown proxy type (%s); must be socks4a or http'%repr(proxytype))

def forwarding_loop(t1, t2, blocksize=1024):
	f1,f2 = t1.fileno(), t2.fileno()
	while True:
		for fd in select.select([f1,f2],[],[])[0]:
			if fd==f1:
				x = t1.recv(blocksize)
				if x=='': return
				t2.send(x)
			elif fd==f2:
				x = t2.recv(blocksize)
				if x=='': return
				t1.send(x)

def string2address(s):
	try:
		host,port = s.split(':')
	except Exception, x:
		raise Exception('badly formed address:port pair (%s)'%repr(s))
	return (host,int(port))

def proxy_chain(args):
	if len(args)<1:
		raise Exception('no arguments')

	s = SocketTransport()
	if args[0]=='listen':
		if len(args)<1:
			raise Exception('need an argument for listen')
		if len(args[1].split(':'))==1:
			addr = ('0.0.0.0',int(args[1]))
		else:
			addr = string2address(args[1])
		s.listen(addr)
		i = 2
	elif args[0]=='connect':
		s.connect(string2address(args[1]))
		i = 2
	else:
		s.connect(string2address(args[0]))
		i = 1
	while i < len(args):
		if args[i]=='socks4a':
			s = SOCKS4ATransport(s)
			s.connect(string2address(args[i+1]))
			i += 2
		elif args[i]=='http':
			s = HTTPTransport(s)
			s.connect(string2address(args[i+1]))
			i += 2
		elif args[i]=='ssl':
			s = SSLTransport(s)
			s.connect(None)
			i += 1
		else:
			raise Exception('unknown transport type %s'%repr(args[i]))
	return s

def isotime():
	return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())

def log(*args):
	print isotime(), ' '.join(map(str,args))
