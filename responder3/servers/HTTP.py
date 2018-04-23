import enum
import logging
import asyncio
from urllib.parse import urlparse

from responder3.core.commons import *
from responder3.protocols.HTTP import *
from responder3.core.servertemplate import ResponderServer, ResponderServerSession


class HTTPServerMode(enum.Enum):
	PROXY = 'PROXY'
	CREDSTEALER = 'CREDSTEALER'


class HTTPSession(ResponderServerSession):
	def __init__(self, connection, log_queue):
		ResponderServerSession.__init__(self, connection, log_queue, self.__class__.__name__)
		self.HTTPVersion = HTTPVersion.HTTP11
		self.HTTPContentEncoding = HTTPContentEncoding.IDENTITY
		self.HTTPConectentCharset = 'utf8'
		self.HTTPAtuhentication = None
		self.HTTPCookie = None
		self.HTTPServerBanner = None
		self.current_state = HTTPState.UNAUTHENTICATED
		self.invisible = False
		self.mode = HTTPServerMode.CREDSTEALER
		self.proxy_closed = asyncio.Event()
		self.SSLintercept = False
		self.close_session = asyncio.Event()
		self.isproxy = False
		self.log_data = False

	def __repr__(self):
		t  = '== HTTPSession ==\r\n'
		t += 'HTTPVersion:      %s\r\n' % repr(self.HTTPVersion)
		t += 'HTTPContentEncoding: %s\r\n' % repr(self.HTTPContentEncoding)
		t += 'HTTPConectentCharset: %s\r\n' % repr(self.HTTPConectentCharset)
		t += 'HTTPAtuhentication: %s\r\n' % repr(self.HTTPAtuhentication)
		t += 'HTTPCookie:       %s\r\n' % repr(self.HTTPCookie)
		t += 'HTTPServerBanner: %s\r\n' % repr(self.HTTPServerBanner)
		t += 'mode:     %s\r\n' % repr(self.mode)
		t += 'isproxy:     %s\r\n' % repr(self.isproxy)
		t += 'current_state:     %s\r\n' % repr(self.current_state)
		return t


class HTTP(ResponderServer):
	def init(self):
		self.parser = HTTPRequest
		self.parse_settings()

	def parse_settings(self):
		if self.settings is None:
			# default settings, basically just NTLM auth
			self.session.mode = HTTPServerMode.CREDSTEALER
			
			# self.session.HTTPAtuhentication   = HTTPNTLMAuth(isProxy = self.session.isproxy)
			# self.session.HTTPAtuhentication.setup()
			self.session.HTTPAtuhentication = HTTPBasicAuth(isProxy=self.session.isproxy)

		else:
			if 'mode' in self.settings:
				self.session.mode = HTTPServerMode(self.settings['mode'].upper())

			if 'authentication' in self.settings:
				# supported authentication mechanisms
				if self.settings['authentication']['authmecha'].upper() == 'NTLM':
					self.session.HTTPAtuhentication   = HTTPNTLMAuth(isProxy = self.session.mode == HTTPServerMode.PROXY)
					if 'settings' in self.settings['authentication']:
						self.session.HTTPAtuhentication.setup(self.settings['authentication']['settings'])
					else:
						self.session.HTTPAtuhentication.setup()
				
				elif self.settings['authmecha'].upper() == 'BASIC':
					self.session.HTTPAtuhentication = HTTPBasicAuth(isProxy = self.session.mode == HTTPServerMode.PROXY)

				else:
					raise Exception('Unsupported HTTP authentication mechanism: %s' % (self.settings['authentication']['authmecha']))

				if 'cerdentials' in self.settings['authentication']:
					self.session.HTTPAtuhentication.verifyCreds = self.settings['authentication']['cerdentials']

	async def parse_message(self, timeout = None):
		try:
			req = await asyncio.wait_for(self.parser.from_streamreader(self.creader), timeout = timeout)
			return req
		except asyncio.TimeoutError:
			await self.log('Timeout!', logging.DEBUG)

	async def send_data(self, data):
		self.cwriter.write(data)
		await self.cwriter.drain()

	async def modify_data(self, data):
		return data

	async def proxy_forwarder(self, reader, writer, laddr, raddr):
		while not self.session.close_session.is_set():
			try:
				data = await asyncio.wait_for(reader.read(1024), timeout=None)
			except asyncio.TimeoutError:
				await self.log('Timeout!', logging.DEBUG)
				self.session.close_session.set()
				break	
			
			if data == b'' or reader.at_eof():
				await self.log('Connection closed!', logging.DEBUG)
				self.session.close_session.set()
				break

			# await self.logProxy('original data: %s' % repr(data), laddr, raddr)
			modified_data = await self.modify_data(data)
			if modified_data != data:
				pass
				# await self.logProxy('modified data: %s' % repr(modified_data),laddr, raddr)
			
			try:
				writer.write(modified_data)
				await asyncio.wait_for(writer.drain(), timeout=1)
			except asyncio.TimeoutError:
				await self.log('Timeout!', logging.DEBUG)
				self.session.close_session.set()
				break
			except OSError as e:
				await self.log('Socket probably got closed!', logging.DEBUG)
				self.session.close_session.set()
				break

		return

	async def httpproxy(self, req):
		# self.session.invisible!!!!

		if req.method == 'CONNECT':
			rhost, rport = req.uri.split(':')
			# https://tools.ietf.org/html/rfc7231#section-4.3.6
			if not self.session.SSLintercept:
				# not intercepting SSL traffic, acting as a generic proxy
				try:
					remote_reader, remote_writer = await asyncio.wait_for(asyncio.open_connection(host=rhost, port=int(rport)), timeout=1)
				except Exception as e:
					await self.log_exception('Failed to create remote connection to %s:%s!' % (rhost, rport))
					return

				# indicating to the client that TCP socket has opened towards the remote host
				await asyncio.wait_for(self.send_data(HTTP200Resp().to_bytes()), timeout = 1)
				self.loop.create_task(self.proxy_forwarder(remote_reader, self.cwriter, '%s:%d' % (rhost,int(rport)), self.session.connection.get_local_address()))
				self.loop.create_task(self.proxy_forwarder(self.creader, remote_writer, self.session.connection.get_local_address(), '%s:%d' % (rhost,int(rport))))
				await asyncio.wait_for(self.session.proxy_closed.wait(), timeout = None)
			
			else:
				print('a')
				while not self.session.close_session.is_set():
					print('aa')
					data = await self.creader.read(-1)
					print('=====request======')
					print(data)

					
					# sending data to remote host
					remote_writer.write(data)
					await remote_writer.drain()

					data_return = await remote_reader.read()
					print('=======response===============')
					print(data_return)

					await asyncio.wait_for(self.send_data(data_return), timeout = 1)

					# req = await asyncio.wait_for(self.parse_message(), timeout = 10)
		
		else:
			while not self.session.close_session.is_set():
				o = urlparse(req.uri)
				if o.netloc.find(':') != -1:
					rhost, rport = o.netloc.split(':')
				else:
					rhost = o.netloc
					rport = 80

				if o.query != '':
					uri = '?'.join([o.path, o.query])
				else:
					uri = o.path
				hdrs = collections.OrderedDict()
				for hdr in req.headers:
					if hdr.lower() == 'proxy-authorization':
						continue
					hdrs[hdr] = req.headers[hdr]
				
				req_new = HTTPRequest.construct(req.method, uri, hdrs, req.body, req.version)
				await self.log('======== request sent ============', logging.DEBUG)
				# print(req_new)

				try:
					remote_reader, remote_writer = await asyncio.wait_for(asyncio.open_connection(host=rhost, port=int(rport)), timeout=1)
				except Exception as e:
					await self.log_exception()
					return
					

				# sending data to remote host
				remote_writer.write(req_new.to_bytes())
				await remote_writer.drain()

				resp = await asyncio.wait_for(HTTPResponse.from_streamreader(remote_reader), timeout = 1)
				await self.log('=== proxyying response ====', logging.DEBUG)
				await asyncio.wait_for(self.send_data(resp.to_bytes()), timeout = None)

				await self.log('=== PROXY === \r\n %s \r\n %s ======' % (req_new, resp))

				if req.props.connection is not None and req.props.connection == HTTPConnection.KEEP_ALIVE:
					print('keepalive!')
					req = await asyncio.wait_for(self.parse_message(timeout = None), timeout = None)
					if req is None:
						self.session.close_session.set()
						return
				else:
					await self.log('Closing connection!', logging.DEBUG)
					self.session.close_session.set()
					remote_writer.close()
					self.cwriter.close()
					return

	async def run(self):
		try:
			while not self.session.close_session.is_set():
				req = await asyncio.wait_for(self.parse_message(), timeout = None)
				if req is None:
					# connection closed exception happened in the parsing
					self.session.close_session.set()
					return

				if 'R3DEEPDEBUG' in os.environ:
					await self.log(req, logging.DEBUG)
					await self.log(repr(self.session), logging.DEBUG)

				if self.session.log_data:
					pass
				
				if self.session.current_state == HTTPState.UNAUTHENTICATED and self.session.HTTPAtuhentication is None:
					self.session.current_state = HTTPState.AUTHENTICATED
				
				if self.session.current_state == HTTPState.UNAUTHENTICATED:
					await self.session.HTTPAtuhentication.do_AUTH(req, self)

				if self.session.current_state == HTTPState.AUTHFAILED:
					await asyncio.wait_for(self.send_data(HTTP403Resp('Auth failed!').to_bytes()), timeout = 1)
					self.cwriter.close()
					return

				if 'R3DEEPDEBUG' in os.environ:
					await self.log(req, logging.DEBUG)
					await self.log(repr(self.session), logging.DEBUG)

				if self.session.current_state == HTTPState.AUTHENTICATED:
					if self.session.mode == HTTPServerMode.PROXY:
						a = await asyncio.wait_for(self.httpproxy(req), timeout = None)
					else:
						# serve page or whatever
						print('sending OK')
						await asyncio.wait_for(self.send_data(HTTP200Resp().to_bytes()), timeout = 1)
						return

		except Exception as e:
			await self.log_exception()
