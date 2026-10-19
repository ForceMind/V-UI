"""Small-packet actual I/O and DNS wire records; no replay or synthetic success."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import base64
import ipaddress
import socket
import struct
import time
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch
import guard_contract as g


def dns_wire(wire):
    g.need(len(wire)>=12,'Truncated DNS evidence')
    ident,flags,qd,an,ns,ar=struct.unpack('!6H',wire[:12]);pos=12
    def name(pos):
        labels=[]
        while True:
            length=wire[pos];pos+=1
            if length==0:return '.'.join(labels),pos
            g.need(length<64 and pos+length<=len(wire),'Unexpected DNS name encoding')
            labels.append(wire[pos:pos+length].decode('ascii'));pos+=length
    g.need(qd==1 and ns==ar==0,'Unexpected DNS evidence sections')
    host,pos=name(pos);kind,klass=struct.unpack('!HH',wire[pos:pos+4]);pos+=4
    g.need(klass==1 and kind in (1,28),'Unexpected DNS question')
    answers=[]
    for _ in range(an):
        g.need(wire[pos:pos+2]==b'\xc0\x0c','Unexpected DNS answer owner');pos+=2
        typ,cl,ttl,size=struct.unpack('!HHIH',wire[pos:pos+10]);pos+=10
        g.need(typ==kind and cl==1 and ttl==0 and size=={1:4,28:16}[kind],'Unexpected DNS answer type')
        answers.append(str(ipaddress.ip_address(wire[pos:pos+size])));pos+=size
    g.need(pos==len(wire),'Trailing DNS evidence bytes')
    return {'id':ident,'flags':flags,'rcode':flags&15,'name':host,'type':kind,'answers':answers}


class Observe:
    def __init__(self,f,row,module,helper,support):
        self.f,self.row,self.module,self.helper,self.support=f,row,module,helper,support
        self.active=None;self.associations=[];self.closed=[];self.dns=[];self.failed_constructions=[]
    def checkpoint(self):
        result={}
        for role in ('domestic','upstream','client'):
            core=getattr(self.f,role,None)
            if core and core.log_path.exists():
                raw=core.log_path.read_text(errors='strict')
                result[role]={'offset':len(raw),'record_boundary':not raw or raw.endswith('\n'),'path':core.log_path.name,'text':raw}
        return result
    def fresh(self,before):
        result={}
        for role,old in before.items():
            core=getattr(self.f,role,None)
            g.need(core and core.log_path.name==old['path'],'Log process changed inside packet operation')
            text=core.log_path.read_text(errors='strict');g.need(text.startswith(old['text']),'Log prefix changed')
            result[role]={'offset':old['offset'],'record_boundary':old['record_boundary'],'path':old['path'],'text':text[old['offset']:]}
        return result
    @contextmanager
    def step(self,name,kind,**fields):
        entry={'id':name,'kind':kind,'started':time.monotonic(),'status':'running',**fields}
        self.row['steps'].append(entry);self.active=entry
        try:
            yield entry
            entry['status']='passed'
        except BaseException as exc:
            entry['status']='failed';entry['error']={'type':type(exc).__name__,'message':str(exc)}
            raise
        finally:
            entry['completed']=time.monotonic();self.active=None
    def register(self,kind,stream,control=None,relay=None,initial=None):
        def fd(s):
            import os
            return {'fd':s.fileno(),'inode':int(os.readlink('/proc/self/fd/'+str(s.fileno())).split('[')[1][:-1]),'local':list(s.getsockname()),'peer':list(s.getpeername()) if s.type&socket.SOCK_STREAM else None}
        record={'id':len(self.associations),'kind':kind,'stream':fd(stream)}
        if control is not None:record['control']=fd(control);record['relay']=list(relay)
        else:record.update(initial=list(initial),tls_verified=stream.context.verify_mode==2 and stream.context.check_hostname,server_name=stream.server_hostname,tls_version=stream.version())
        self.associations.append(record);return record
    def socket(self,stream,association):
        owner=self
        class Proxy:
            def __getattr__(self,key):return getattr(stream,key)
            def sendto(self,wire,dest):return self._send('sendto',wire,dest)
            def sendall(self,wire):return self._send('sendall',wire,None)
            def _send(self,method,wire,dest):
                g.need(owner.active is not None,'Application send outside planned operation')
                before=owner.checkpoint();event={'method':method,'wire_hex':wire.hex(),'wire':g.ident(wire),'destination':list(dest) if dest else None,'association':association['id'],'started':time.monotonic()}
                owner.active.setdefault('sends',[]).append(event)
                try:
                    value=getattr(stream,method)(wire,*([dest] if dest is not None else []));event['return']=value;return value
                except OSError as exc:event['errno']=exc.errno;raise
                finally:
                    event['completed']=time.monotonic();event['log_checkpoint']=before
            def recv(self,size):return self._recv('recv',size)
            def recvfrom(self,size):return self._recv('recvfrom',size)
            def _recv(self,method,size):
                try:value=getattr(stream,method)(size)
                except (socket.timeout,ConnectionRefusedError,ConnectionResetError) as exc:
                    if owner.active is not None:owner.active.setdefault('receive_errors',{}).setdefault(type(exc).__name__,0);owner.active['receive_errors'][type(exc).__name__]+=1
                    raise
                wire=value[0] if isinstance(value,tuple) else value
                event={'method':method,'wire_hex':wire.hex(),'wire':g.ident(wire),'association':association['id'],'completed':time.monotonic()}
                if isinstance(value,tuple):event['peer']=list(value[1])
                g.need(owner.active is not None,'Receive outside planned operation');owner.active.setdefault('receives',[]).append(event)
                return value
        return Proxy()
    @contextmanager
    def install(self):
        from socks_observation import observable_socks_class
        owner=self;old_socks=observable_socks_class(self.helper);old_raw=self.module.RawXudpAssociation
        class Socks(old_socks):
            def __enter__(self):
                value=super().__enter__();self.observed_stream=self.stream
                try:
                    self.observation=owner.register('socks',self.stream,self.control,self.relay)
                    self.stream=owner.socket(self.stream,self.observation);return value
                except BaseException:
                    self.stack.close();owner.failed_constructions.append({'kind':'socks','stream_fd':self.observed_stream.fileno(),'control_fd':self.control.fileno()});raise
            def __exit__(self,*args):
                try:return super().__exit__(*args)
                finally:owner.closed.append({'id':self.observation['id'],'stream_fd':self.observed_stream.fileno(),'control_fd':self.control.fileno()})
        class Raw(old_raw):
            def __init__(self,*args):
                super().__init__(*args);self.observed_stream=self.stream
                try:
                    self.observation=owner.register('raw',self.stream,initial=self.initial);self.stream=owner.socket(self.stream,self.observation)
                except BaseException:
                    self.stack.close();owner.failed_constructions.append({'kind':'raw','stream_fd':self.observed_stream.fileno()});raise
            def __exit__(self,*args):
                try:return super().__exit__(*args)
                finally:owner.closed.append({'id':self.observation['id'],'stream_fd':self.observed_stream.fileno()})
            def receive(self):
                value=super().receive();owner.active['decoded']={'host':value[0],'port':value[1],'payload':g.ident(value[2])};return value
        # The fixture module and test module hold separate imported class refs.
        import sys
        fixture_module=sys.modules['restricted_udp_preflight_helpers']
        with patch.object(fixture_module,'SocksDatagrams',Socks),patch.object(fixture_module,'RawXudpAssociation',Raw),patch.object(self.module,'RawXudpAssociation',Raw):
            yield
    def doh(self,stack,answers,cert,key):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_POST(self):
                size=int(self.headers.get('Content-Length','0'));g.need(0<size<=65535,'Bad DoH request size');self.respond(self.rfile.read(size))
            def do_GET(self):
                encoded=parse_qs(urlsplit(self.path).query).get('dns',[''])[0];self.respond(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
            def respond(self,packet):
                g.need(urlsplit(self.path).path=='/dns-query','Wrong DoH path')
                data=answers.answer(packet,self.client_address[0]);entry={'peer':self.client_address[0],'query_hex':packet.hex(),'response_hex':data.hex(),'query':dns_wire(packet),'response':dns_wire(data),'started':time.monotonic()}
                owner.dns.append(entry)
                self.send_response(200);self.send_header('Content-Type','application/dns-message');self.send_header('Content-Length',str(len(data)));self.end_headers()
                try:entry['write_return']=self.wfile.write(data);self.wfile.flush()
                except Exception as exc:entry['error']=type(exc).__name__;raise
                finally:entry['completed']=time.monotonic()
        return self.support.tracked_https_server(stack,self.support.DOH,Handler,cert,key,answers)
