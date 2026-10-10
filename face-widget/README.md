# face-widget

Halden's face as a desktop widget: a small, frameless, always-on-top,
genuinely transparent window that floats the ring over the desktop.

It is not a second visualizer. It loads a face from the running
ai-visualizer server, so it rides the same signal bus the browser faces
ride and idles, listens, thinks and speaks in lockstep with the voice
line, with no extra wiring.

## Running it

Desktop shortcut **Halden face**, or `run.bat` here.

The visualizer server is started automatically if nothing is listening on
port 8790, so this is one launcher, not two. It deliberately does **not**
stop the server on exit: a browser face or the gallery may still be using
it.

There is no title bar and no taskbar entry, so the ways out are:

- **Right-click the widget** for size, opacity, always-on-top, reset
  position, reload, and quit.
- **The tray icon** (a green ring) for show, hide, and quit. Clicking it
  toggles the widget.

**Dragging:** grab it anywhere and move it. The whole square is
draggable, including the parts you cannot see, which is also the
tradeoff: the window blocks clicks on whatever is beneath it across its
full rectangle, not just where the ring is drawn. Park it somewhere
empty. Making the transparent region click-through is possible later
with one Win32 call, if it ever grates.

**Resizing:** the mouse wheel over the widget, or the right-click menu.

## Configuration

`face-widget.json`. Everything the menus change is saved back here, so
you should rarely need to edit it by hand.

| key | meaning |
| --- | --- |
| `face` | folder name under the visualizer's `faces/`. Default `radial-widget`. |
| `port` | visualizer port. Default 8790. |
| `size` | width and height in logical pixels. |
| `pos_x`, `pos_y` | saved position; `null` means place it bottom right. |
| `always_on_top` | float above other windows. |
| `autostart_server` | start `server.py` if the port is dead. |
| `opacity` | whole-widget fade, 0.15 to 1.0. |
| `visualizer_dir` | where the ai-visualizer checkout lives. |

## The face has to be a transparent one

The stock faces all paint a full-bleed background, so pointing this at
`radial` or `board` gives you an opaque rectangle, correctly rendered and
completely wrong for a widget.

`faces/radial-widget/` in the ai-visualizer folder is the transparent
variant, derived from `radial` with the background, the galaxy backdrop
and the full-canvas alert wash removed. Its header lists the complete
ten-point diff from the original so it can be re-derived rather than
hand-merged when upstream moves.

To make another one, copy a face folder, apply the same ten points, and
point `face` at it. The server discovers faces by scanning the folder,
so nothing else needs to change.

## Why PySide6, and why it is 645 MB

The honest answer is that the cheap options do not work.

WebView2 is already installed on this machine and **pywebview** can host
it in about three lines, at a cost of a few megabytes. Its `transparent`
flag really does make the WebView2's own background transparent. But its
Windows host is a WinForms form that paints an opaque background behind
the browser, and that background cannot be removed: setting the form's
`TransparencyKey` succeeds, reports no error, and changes nothing on
screen, because WebView2 composites through DirectComposition and does
not participate in layered-window colour keying. Measured on this
machine on 2026-08-27, not assumed.

Qt renders the page into a surface it owns, so `WA_TranslucentBackground`
has something to act on and the result is real per-pixel alpha, including
through soft glow falloff that colour keying would have wrecked. The
price is that QtWebEngine carries its own Chromium.

Electron would also have worked and costs a Node toolchain plus its own
Chromium. Tauri would have been far smaller at runtime, since it would
reuse the WebView2 already present, but needs Rust and the MSVC build
tools to build. Neither toolchain is installed and PySide6 needed only a
pip install, so that is what this is.

## When something is wrong

`widget.log`, in this folder. The launcher runs under `pythonw`, which
has no console, so everything worth reading afterwards is written there,
including a full traceback for an unhandled crash.

- **Nothing appears.** Check the log. The usual cause is the visualizer
  not starting; the widget says so in a dialog and writes the reason.
- **A grey or black square instead of a ring.** The `face` is pointing at
  a face that paints a background. Use `radial-widget`.
- **It vanished after a monitor changed.** Saved positions are validated
  against the screens that currently exist, and a position no longer
  covered by any screen falls back to the bottom right of the primary.
  Right-click, reset position.
- **"Already running".** One copy at a time, enforced with a `QLockFile`.
  The tray icon belongs to the copy that is already up.
