import sys
import socket
import OpenSSL
import Proxies
import urlparse
import StringIO
import signal

def recv_line(sock):
	s = StringIO.StringIO()
	while True:
		c = sock.recv(1)
		if c=='\n': break
		s.write(c)
	x = s.getvalue()
	if len(x) and x[-1]=='\r':
		x = x[:-1]
	return x

def do_https_query(url, postdata=None, reqheaders=None, digest=None, digesttype='sha256', proxy=None, proxytype=None, timeout=None):
	scheme,netloc,path,query,fragment = urlparse.urlsplit(url)
	if scheme<>'https':
		raise Exception('not an HTTPS query')

	if len(netloc.split('@'))>2:
		raise Exception('username/password not supported')

	hp = netloc.split(':')
	if len(hp)>2 or len(hp)<1:
		raise Exception('netloc must be hostname:port')

	host = hp[0]
	if len(hp)>=2:
		port = int(hp[1])
	else:
		port = 443

	if timeout<>None:
		signal.alarm(timeout)

	s = socket.socket()

	if proxytype<>None:
		s.connect(proxy)
		Proxies.do_proxy_connect(s,proxytype,host,port)
	else:
		s.connect((host,port))

	ctx = OpenSSL.SSL.Context(OpenSSL.SSL.TLSv1_METHOD)
	c = OpenSSL.SSL.Connection(ctx,s)
	c.set_connect_state()
	c.do_handshake()

	if timeout<>None:
		signal.alarm(0)

	if digest<>None:
		cert = c.get_peer_certificate()
		digestgot = cert.digest(digesttype)
		if digestgot<>digest:
			raise Exception('server certificate mismatch (%s)'%digestgot)
	
	result = do_http_query(c, host, path+query, postdata, headers=reqheaders)

	c.close()
	return result

def do_http_query(c, host, path, postdata=None, headers=None):
	#
	# send query
	#
	fullquery=path.replace(' ','%20')
	if postdata<>None:
		method = 'POST'
	else:
		method = 'GET'
	httpquery = [
		'%s %s HTTP/1.1'%(method,fullquery),
		'Host: %s'%host,
		'User-Agent: script',
		'Content-Type: application/x-www-form-urlencoded',
	]
	if postdata<>None:
		httpquery.append('Content-Length: %d'%len(postdata))
	if headers==None:
		headers = []
	headerdata = '\r\n'.join(httpquery+headers)+'\r\n\r\n'

	i = c.send(headerdata)
	while i < len(headerdata):
		nsent = c.send(headerdata[i:])
		i += nsent

	if postdata<>None:
		i = c.send(postdata)
		while i < len(postdata):
			nsent = c.send(postdata[i:])
			i += nsent

	#
	# get response
	#
	first = recv_line(c)
	ff = first.split()
	if len(ff)<2 or ff[1]<>'200':
		raise Exception('server did not say 200 (%s)'%first)

	repheaders = [first]
	content_length = None
	transfer_encoding = None
	while True:
		header = recv_line(c)
		if not header: break
		repheaders.append(header)
		nv = header.split(':')
		if len(nv)==2:
			name = nv[0].strip().lower()
			value = nv[1].strip()
			if name=='content-length':
				content_length = int(value)
			if name=='transfer-encoding':
				transfer_encoding = value

	s = StringIO.StringIO()
	if transfer_encoding=='chunked':
		while True:
			chunkhead = recv_line(c)
			chunklen = int(chunkhead.split(';')[0], 16)

			n = 0
			while n<chunklen:
				block = c.recv(chunklen-n)
				if block=='': break
				n += len(block)
				s.write(block)

			recv_line(c)

			if chunklen==0: break
	elif content_length<>None:
		while s.len<content_length:
			block = c.recv(min(1024,content_length-s.len))
			if block=='': break
			s.write(block)
	else:
		while True:
			block = c.recv(1024)
			if block=='': break
			s.write(block)

	return (repheaders,s.getvalue())
