"""Owned D-Bus portal for negative responses; stdlib ctypes and system GIO."""
import ctypes as C
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

P, S, I, U = C.c_void_p, C.c_char_p, C.c_int, C.c_uint
gio = C.CDLL('libgio-2.0.so.0')
glib = C.CDLL('libglib-2.0.so.0')
def bind(lib, name, result, args):
    fn = getattr(lib, name); fn.restype = result; fn.argtypes = args
    return fn
bus = bind(gio, 'g_bus_get_sync', P, [I,P,P])
parse = bind(glib, 'g_variant_parse', P, [P,S,P,P,P])
child = bind(glib, 'g_variant_get_child_value', P, [P,C.c_size_t])
handle = bind(glib, 'g_variant_get_handle', I, [P])
lookup = bind(glib, 'g_variant_lookup_value', P, [P,S,P])
string = bind(glib, 'g_variant_get_string', S, [P,P])
get_message = bind(gio, 'g_dbus_method_invocation_get_message', P, [P])
get_fds = bind(gio, 'g_dbus_message_get_unix_fd_list', P, [P])
get_fd = bind(gio, 'g_unix_fd_list_get', I, [P,I,P])
node_info = bind(gio, 'g_dbus_node_info_new_for_xml', P, [S,P])
return_value = bind(gio, 'g_dbus_method_invocation_return_value', None, [P,P])
call = bind(gio, 'g_dbus_connection_call_sync', P, [P,S,S,S,S,P,P,I,I,P,P])
emit = bind(gio, 'g_dbus_connection_emit_signal', I, [P,S,S,S,S,P,P])
iterate = bind(glib, 'g_main_context_iteration', I, [P,I])

class NodeInfo(C.Structure):
    _fields_ = [('refs',I),('path',S),('interfaces',C.POINTER(P))]
callback_type = C.CFUNCTYPE(None, P,S,S,S,S,P,P,P)
class VTable(C.Structure):
    _fields_ = [('method',callback_type),('get',P),('set',P),('padding',P*8)]
register = bind(gio, 'g_dbus_connection_register_object', U, [P,S,P,C.POINTER(VTable),P,P,P])

def interface(xml):
    info = node_info(xml.encode(), None)
    assert info
    return C.cast(info, C.POINTER(NodeInfo)).contents.interfaces[0]

def variant(text):
    result = parse(None, text.encode(), None, None, None)
    assert result, text
    return result

connection = bus(2,None,None)
assert connection
reply = call(connection, b'org.freedesktop.DBus', b'/org/freedesktop/DBus', b'org.freedesktop.DBus', b'RequestName',
             variant("('org.freedesktop.portal.Desktop', uint32 0)"),None,0,1000,None,None)
assert reply
portal_info = interface('<node><interface name="org.freedesktop.portal.OpenURI"><method name="OpenFile"><arg type="s" direction="in"/><arg type="h" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method></interface></node>')
request_info = interface('<node><interface name="org.freedesktop.portal.Request"><method name="Close"/><signal name="Response"><arg type="u"/><arg type="a{sv}"/></signal></interface></node>')
requests, closed = [], []
mode = sys.argv[1]

@callback_type
def method(conn, sender, path, iface, member, params, invocation, data):
    if member == b'Close':
        closed.append(True)
        return_value(invocation, variant('()'))
        return
    descriptor = handle(child(params,1))
    fd = get_fd(get_fds(get_message(invocation)),descriptor,None)
    assert fd >= 0 and os.path.isdir('/proc/self/fd/%d' % fd)
    os.close(fd)
    token = string(lookup(child(params,2),b'handle_token',None),None)
    path = b'/org/freedesktop/portal/desktop/request/' + sender[1:].replace(b'.',b'_') + b'/' + token
    assert register(connection,path,request_info,C.byref(table),None,None,None)
    requests.append([sender,path,time.monotonic(),False])
    return_value(invocation, variant("(objectpath '%s',)" % path.decode()))

table = VTable(method,None,None)
assert register(connection,b'/org/freedesktop/portal/desktop',portal_info,C.byref(table),None,None,None)
repo = Path(__file__).resolve().parents[1]
source = ('import os; from raman_portal import open_directory; '
          'fd=os.open(%r,os.O_RDONLY|os.O_DIRECTORY); open_directory(fd,timeout=0.3)' % str(repo))
process = subprocess.Popen([sys.executable,'-c',source],cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
started, cancelled = time.monotonic(), False
while process.poll() is None and time.monotonic()-started < 4:
    iterate(None,False)
    if mode == 'reject':
        for request in requests:
            if not request[3] and time.monotonic()-request[2] > .03:
                emit(connection,request[0],request[1],b'org.freedesktop.portal.Request',b'Response',variant('(uint32 2, @a{sv} {})'),None)
                request[3] = True
    elif mode == 'cancel' and requests and not cancelled:
        process.send_signal(signal.SIGTERM); cancelled = True
    time.sleep(.005)
if process.poll() is None: process.kill()
stdout, stderr = process.communicate(timeout=1)
assert process.returncode != 0, 'method receipt incorrectly treated as success'
assert b'PortalError' in stderr, stderr
assert requests, 'FD handoff did not reach portal'
assert closed, 'unfinished request was not closed'
print(mode + ': rejected success-only receipt; request closed')
