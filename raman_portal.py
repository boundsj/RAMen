"""Bounded directory-only desktop portal handoff using stdlib ctypes + system GIO.

OpenFile accepts a directory FD (OpenDirectory opens its *parent*). D-Bus sends
SCM_RIGHTS; no process-relative descriptor path is dispatched to the desktop.
Only Response(0), not the method's request-handle receipt, means success.
"""
import ctypes as C
import os
import signal
import stat
import time


class PortalError(Exception):
    pass


def open_directory(fd, timeout=5):
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        raise PortalError("portal handoff requires a directory")
    try:
        gio = C.CDLL("libgio-2.0.so.0")
        glib = C.CDLL("libglib-2.0.so.0")
        obj = C.CDLL("libgobject-2.0.so.0")
    except OSError as exc:
        raise PortalError("system GIO desktop portal support unavailable") from exc
    P, S, I, U = C.c_void_p, C.c_char_p, C.c_int, C.c_uint
    def bind(lib, name, result, args):
        fn = getattr(lib, name); fn.restype = result; fn.argtypes = args
        return fn
    bus = bind(gio, "g_bus_get_sync", P, [I, P, P])
    unique = bind(gio, "g_dbus_connection_get_unique_name", S, [P])
    fd_new = bind(gio, "g_unix_fd_list_new", P, [])
    fd_append = bind(gio, "g_unix_fd_list_append", I, [P, I, P])
    parse = bind(glib, "g_variant_parse", P, [P, S, P, P, P])
    child = bind(glib, "g_variant_get_child_value", P, [P, C.c_size_t])
    uint = bind(glib, "g_variant_get_uint32", U, [P])
    type_string = bind(glib, "g_variant_get_type_string", S, [P])
    string = bind(glib, "g_variant_get_string", S, [P, P])
    unref = bind(glib, "g_variant_unref", None, [P])
    object_unref = bind(obj, "g_object_unref", None, [P])
    call = bind(gio, "g_dbus_connection_call_with_unix_fd_list_sync", P,
                [P, S, S, S, S, P, P, I, I, P, P, P, P])
    callback_type = C.CFUNCTYPE(None, P, S, S, S, S, P, P)
    subscribe = bind(gio, "g_dbus_connection_signal_subscribe", U,
                     [P, S, S, S, S, S, I, callback_type, P, P])
    unsubscribe = bind(gio, "g_dbus_connection_signal_unsubscribe", None, [P, U])
    iterate = bind(glib, "g_main_context_iteration", I, [P, I])
    response = []
    @callback_type
    def received(connection, sender, path, interface, member, params, data):
        if type_string(params) != b"(ua{sv})":
            response.append(2); return
        value = child(params, 0)
        response.append(uint(value)); unref(value)
    connection = bus(2, None, None)  # G_BUS_TYPE_SESSION
    if not connection:
        raise PortalError("desktop session bus unavailable")
    token = "raman_" + os.urandom(12).hex()
    request = b"/org/freedesktop/portal/desktop/request/" + unique(connection)[1:].replace(b".", b"_") + b"/" + token.encode()
    destination = b"org.freedesktop.portal.Desktop"
    subscription = subscribe(connection, destination, b"org.freedesktop.portal.Request", b"Response", request, None, 0, received, None, None)
    fds = fd_new()
    pending = False
    previous = None
    def interrupted(signum, frame):
        raise PortalError("directory handoff cancelled")
    try:
        previous = signal.signal(signal.SIGTERM, interrupted)
        if fd_append(fds, fd, None) != 0:
            raise PortalError("directory FD transfer unavailable")
        params = parse(None, ("('', handle 0, {'handle_token': <'%s'>})" % token).encode(), None, None, None)
        if not params:
            raise PortalError("invalid portal parameters")
        pending = True
        deadline = time.monotonic() + timeout
        reply = call(connection, destination, b"/org/freedesktop/portal/desktop", b"org.freedesktop.portal.OpenURI", b"OpenFile",
                     params, None, 0, int(timeout * 1000), fds, None, None, None)
        if not reply:
            raise PortalError("desktop portal refused directory opening or is unavailable")
        handle = child(reply, 0)
        returned = string(handle, None)
        unref(handle); unref(reply)
        if returned != request:
            request = returned
            raise PortalError("desktop portal lacks predictable request handles")
        while not response and time.monotonic() < deadline:
            iterate(None, False)
            time.sleep(0.01)
        if not response or response[0] != 0:
            raise PortalError("desktop portal directory handoff timed out, cancelled or failed")
        pending = False
    finally:
        if pending:
            # Cancel an unfinished request. A successful desktop handoff belongs
            # to the portal/file manager, never to RAMen's worker process group.
            reply = call(connection, destination, request, b"org.freedesktop.portal.Request", b"Close",
                         None, None, 0, 200, None, None, None, None)
            if reply: unref(reply)
        if previous is not None: signal.signal(signal.SIGTERM, previous)
        unsubscribe(connection, subscription)
        object_unref(fds); object_unref(connection)
