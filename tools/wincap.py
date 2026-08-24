#!/usr/bin/env python3
"""WINDOW CAPTURE THAT DOES NOT LIE.

Every screenshot tool on this box (`maim -i`, `import -window`) captures a
SCREEN REGION where the window happens to sit, so a covered window comes
back showing whatever is on top of it -- proved: covering a green window
with a red one and capturing the green xid returned pure red.

This module captures the window's OWN content instead, by asking the X
compositor for the offscreen pixmap that picom is already keeping for it
(XCompositeNameWindowPixmap). That pixmap holds what the window drew,
whether or not anything is in front of it.

The one thing it CANNOT do is capture an UNMAPPED window. i3 unmaps
windows on hidden workspaces, and also unmaps tiled windows that a
fullscreen window covers -- WM_STATE goes Withdrawn, the pixmap is freed,
and there is nothing left to read. That is a property of X11, not a bug
here. `state()` reports it so callers can say so honestly.

ctypes note (this cost a segfault once): every X function that returns a
pointer or takes one MUST have restype/argtypes declared, or 64-bit
pointers get truncated to int and the process dies.
"""
import ctypes
import ctypes.util
import sys

# ---------------------------------------------------------------- X types
Display = ctypes.c_void_p
XID = ctypes.c_ulong
Window = XID
Pixmap = XID
Drawable = XID
Atom = XID

ZPIXMAP = 2          # XImage format: packed pixels, one plane
ALLPLANES = (1 << 64) - 1
IS_VIEWABLE = 2      # XWindowAttributes.map_state


class XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
        ("data", ctypes.POINTER(ctypes.c_char)),
        ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
        ("obdata", ctypes.c_void_p),
        # the function table that follows is never touched from here
    ]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", Window),
        ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", XID), ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


def _lib(name):
    path = ctypes.util.find_library(name)
    if not path:
        raise RuntimeError("lib%s not found" % name)
    return ctypes.CDLL(path)


_x11 = _lib("X11")
_xc = _lib("Xcomposite")

_x11.XOpenDisplay.restype = Display
_x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
_x11.XCloseDisplay.restype = ctypes.c_int
_x11.XCloseDisplay.argtypes = [Display]
_x11.XGetWindowAttributes.restype = ctypes.c_int
_x11.XGetWindowAttributes.argtypes = [Display, Window,
                                      ctypes.POINTER(XWindowAttributes)]
_x11.XGetImage.restype = ctypes.POINTER(XImage)
_x11.XGetImage.argtypes = [Display, Drawable, ctypes.c_int, ctypes.c_int,
                           ctypes.c_uint, ctypes.c_uint,
                           ctypes.c_ulong, ctypes.c_int]
_x11.XDestroyImage.restype = ctypes.c_int
_x11.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
_x11.XFreePixmap.restype = ctypes.c_int
_x11.XFreePixmap.argtypes = [Display, Pixmap]
_x11.XSync.restype = ctypes.c_int
_x11.XSync.argtypes = [Display, ctypes.c_int]
_x11.XSetErrorHandler.restype = ctypes.c_void_p
_x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
_x11.XInternAtom.restype = Atom
_x11.XInternAtom.argtypes = [Display, ctypes.c_char_p, ctypes.c_int]
_x11.XGetWindowProperty.restype = ctypes.c_int
_x11.XGetWindowProperty.argtypes = [
    Display, Window, Atom, ctypes.c_long, ctypes.c_long, ctypes.c_int, Atom,
    ctypes.POINTER(Atom), ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
    ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
_x11.XFree.restype = ctypes.c_int
_x11.XFree.argtypes = [ctypes.c_void_p]

_xc.XCompositeQueryExtension.restype = ctypes.c_int
_xc.XCompositeQueryExtension.argtypes = [Display, ctypes.POINTER(ctypes.c_int),
                                         ctypes.POINTER(ctypes.c_int)]
_xc.XCompositeNameWindowPixmap.restype = Pixmap
_xc.XCompositeNameWindowPixmap.argtypes = [Display, Window]
_xc.XCompositeRedirectWindow.restype = ctypes.c_int
_xc.XCompositeRedirectWindow.argtypes = [Display, Window, ctypes.c_int]

# X errors are asynchronous and fatal-by-default through ctypes; swallow
# them so a window that dies mid-capture raises a normal Python error
# instead of taking the whole process down.
_ERRH = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_last_err = {"code": 0}


def _on_error(dpy, ev):
    _last_err["code"] = 1
    return 0


_ERRH_REF = _ERRH(_on_error)


class Capture:
    """One X connection, reused across captures (opening one per frame
    costs ~1 ms and leaks server-side resources under load)."""

    def __init__(self, display=None):
        name = display.encode() if display else None
        self.dpy = _x11.XOpenDisplay(name)
        if not self.dpy:
            raise RuntimeError("cannot open X display %r" % (display or "$DISPLAY"))
        _x11.XSetErrorHandler(ctypes.cast(_ERRH_REF, ctypes.c_void_p))
        ev, er = ctypes.c_int(), ctypes.c_int()
        self.have_composite = bool(_xc.XCompositeQueryExtension(
            self.dpy, ctypes.byref(ev), ctypes.byref(er)))

    def close(self):
        if self.dpy:
            _x11.XCloseDisplay(self.dpy)
            self.dpy = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------- state
    def state(self, xid):
        """(mapped, w, h, wm_state) -- mapped is the only thing that
        decides whether a capture can possibly work."""
        at = XWindowAttributes()
        _last_err["code"] = 0
        ok = _x11.XGetWindowAttributes(self.dpy, Window(xid), ctypes.byref(at))
        if not ok or _last_err["code"]:
            return (False, 0, 0, "gone")
        wm = self._wm_state(xid)
        return (at.map_state == IS_VIEWABLE, at.width, at.height, wm)

    def _wm_state(self, xid):
        a = _x11.XInternAtom(self.dpy, b"WM_STATE", 1)
        if not a:
            return "?"
        ty, fmt = Atom(), ctypes.c_int()
        n, after = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        r = _x11.XGetWindowProperty(self.dpy, Window(xid), a, 0, 2, 0, 0,
                                    ctypes.byref(ty), ctypes.byref(fmt),
                                    ctypes.byref(n), ctypes.byref(after),
                                    ctypes.byref(data))
        if r != 0 or not data:
            return "?"
        v = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint32))[0]
        _x11.XFree(data)
        return {0: "Withdrawn", 1: "Normal", 3: "Iconic"}.get(v, str(v))

    # ----------------------------------------------------------- capture
    def grab(self, xid, want_composite=True):
        """Return (PIL.Image, source) where source is 'composite' (the
        window's own pixmap -- correct even when covered) or 'screen' (the
        window drawable, which a compositor-less X will fill with whatever
        is in front). Raises if the window is unmapped."""
        from PIL import Image

        mapped, w, h, wm = self.state(xid)
        if not mapped:
            raise LookupError("window 0x%x is not mapped (WM_STATE=%s) -- "
                              "nothing to capture" % (xid, wm))
        if w <= 0 or h <= 0:
            raise LookupError("window 0x%x has no area" % xid)

        drawable, pix, source = Window(xid), None, "screen"
        if want_composite and self.have_composite:
            _last_err["code"] = 0
            p = _xc.XCompositeNameWindowPixmap(self.dpy, Window(xid))
            _x11.XSync(self.dpy, 0)
            if p and not _last_err["code"]:
                pix, drawable, source = p, Pixmap(p), "composite"

        try:
            _last_err["code"] = 0
            img = _x11.XGetImage(self.dpy, drawable, 0, 0, w, h,
                                 ctypes.c_ulong(ALLPLANES), ZPIXMAP)
            if not img or _last_err["code"]:
                raise RuntimeError("XGetImage failed on 0x%x (%s)" % (xid, source))
            im = img.contents
            stride, bpp = im.bytes_per_line, im.bits_per_pixel
            if bpp not in (24, 32):
                _x11.XDestroyImage(img)
                raise RuntimeError("unsupported depth %d bpp" % bpp)
            raw = ctypes.string_at(im.data, stride * im.height)
            mode = "BGRX" if bpp == 32 else "BGR"
            out = Image.frombuffer("RGB", (im.width, im.height), raw,
                                   "raw", mode, stride, 1)
            # frombuffer keeps a view on `raw`; copy before the X image dies
            out = out.copy()
            _x11.XDestroyImage(img)
            return out, source
        finally:
            if pix:
                _x11.XFreePixmap(self.dpy, Pixmap(pix))


def grab_once(xid, path=None, display=None):
    with Capture(display) as c:
        im, src = c.grab(xid)
        if path:
            im.save(path)
        return im, src


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: wincap.py <xid|0xhex> [out.png]", file=sys.stderr)
        sys.exit(2)
    a = sys.argv[1]
    xid = int(a, 16) if a.lower().startswith("0x") else int(a)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        with Capture() as c:
            mapped, w, h, wm = c.state(xid)
            print("xid=0x%x mapped=%s %dx%d WM_STATE=%s composite=%s"
                  % (xid, mapped, w, h, wm, c.have_composite))
            im, src = c.grab(xid)
            px = im.resize((1, 1)).getpixel((0, 0))
            print("captured %dx%d via %s  mean_rgb=%s" % (im.width, im.height, src, px))
            if out:
                im.save(out)
                print("wrote", out)
    except Exception as e:
        print("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
