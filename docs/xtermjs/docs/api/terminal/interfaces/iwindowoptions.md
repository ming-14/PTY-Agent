# Interface: IWindowOptions

Enable various window manipulation and report features (`CSI Ps ; Ps ; Ps t`).

Most settings have no default implementation, as they heavily rely on the embedding environment.

To implement a feature, create a custom CSI hook like this:
    
    
    term.parser.addCsiHandler({final: 't'}, params => {
      const ps = params[0];
      switch (ps) {
        case XY:
          ...            // your implementation for option XY
          return true;   // signal Ps=XY was handled
      }
      return false;      // any Ps that was not handled
    });
    

Note on security: Most features are meant to deal with some information of the host machine where the terminal runs on. This is seen as a security risk possibly leaking sensitive data of the host to the program in the terminal. Therefore all options (even those without a default implementation) are guarded by the boolean flag and disabled by default.

## Hierarchy

  * **IWindowOptions**


## Index

### Properties

  * [fullscreenWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-fullscreenwin)
  * [getCellSizePixels](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getcellsizepixels)
  * [getIconTitle](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-geticontitle)
  * [getScreenSizeChars](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getscreensizechars)
  * [getScreenSizePixels](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getscreensizepixels)
  * [getWinPosition](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getwinposition)
  * [getWinSizeChars](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getwinsizechars)
  * [getWinSizePixels](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getwinsizepixels)
  * [getWinState](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getwinstate)
  * [getWinTitle](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-getwintitle)
  * [lowerWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-lowerwin)
  * [maximizeWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-maximizewin)
  * [minimizeWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-minimizewin)
  * [popTitle](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-poptitle)
  * [pushTitle](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-pushtitle)
  * [raiseWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-raisewin)
  * [refreshWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-refreshwin)
  * [restoreWin](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-restorewin)
  * [setWinLines](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-setwinlines)
  * [setWinPosition](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-setwinposition)
  * [setWinSizeChars](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-setwinsizechars)
  * [setWinSizePixels](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/#optional-setwinsizepixels)


## Properties

### `Optional` fullscreenWin

• **fullscreenWin**? : _boolean_

_Defined in[xterm.d.ts:739](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L739)_

Ps=10 ; 0 Undo full-screen mode. Ps=10 ; 1 Change to full-screen. Ps=10 ; 2 Toggle full-screen. No default implementation.

* * *

### `Optional` getCellSizePixels

• **getCellSizePixels**? : _boolean_

_Defined in[xterm.d.ts:767](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L767)_

Ps=16 Report xterm character cell size in pixels. Result is “CSI 6 ; height ; width t”. Has a default implementation.

* * *

### `Optional` getIconTitle

• **getIconTitle**? : _boolean_

_Defined in[xterm.d.ts:782](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L782)_

Ps=20 Report xterm window’s icon label. Result is “OSC L label ST”. No default implementation.

* * *

### `Optional` getScreenSizeChars

• **getScreenSizeChars**? : _boolean_

_Defined in[xterm.d.ts:777](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L777)_

Ps=19 Report the size of the screen in characters. Result is “CSI 9 ; height ; width t”. No default implementation.

* * *

### `Optional` getScreenSizePixels

• **getScreenSizePixels**? : _boolean_

_Defined in[xterm.d.ts:762](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L762)_

Ps=15 Report size of the screen in pixels. Result is “CSI 5 ; height ; width t”. No default implementation.

* * *

### `Optional` getWinPosition

• **getWinPosition**? : _boolean_

_Defined in[xterm.d.ts:751](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L751)_

Ps=13 Report xterm window position. Result is “CSI 3 ; x ; y t”. Ps=13 ; 2 Report xterm text-area position. Result is “CSI 3 ; x ; y t”. No default implementation.

* * *

### `Optional` getWinSizeChars

• **getWinSizeChars**? : _boolean_

_Defined in[xterm.d.ts:772](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L772)_

Ps=18 Report the size of the text area in characters. Result is “CSI 8 ; height ; width t”. Has a default implementation.

* * *

### `Optional` getWinSizePixels

• **getWinSizePixels**? : _boolean_

_Defined in[xterm.d.ts:757](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L757)_

Ps=14 Report xterm text area size in pixels. Result is “CSI 4 ; height ; width t”. Ps=14 ; 2 Report xterm window size in pixels. Result is “CSI 4 ; height ; width t”. Has a default implementation.

* * *

### `Optional` getWinState

• **getWinState**? : _boolean_

_Defined in[xterm.d.ts:745](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L745)_

Ps=11 Report xterm window state. If the xterm window is non-iconified, it returns “CSI 1 t”. If the xterm window is iconified, it returns “CSI 2 t”. No default implementation.

* * *

### `Optional` getWinTitle

• **getWinTitle**? : _boolean_

_Defined in[xterm.d.ts:787](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L787)_

Ps=21 Report xterm window’s title. Result is “OSC l label ST”. No default implementation.

* * *

### `Optional` lowerWin

• **lowerWin**? : _boolean_

_Defined in[xterm.d.ts:714](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L714)_

Ps=6 Lower the xterm window to the bottom of the stacking order. No default implementation.

* * *

### `Optional` maximizeWin

• **maximizeWin**? : _boolean_

_Defined in[xterm.d.ts:732](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L732)_

Ps=9 ; 0 Restore maximized window. Ps=9 ; 1 Maximize window (i.e., resize to screen size). Ps=9 ; 2 Maximize window vertically. Ps=9 ; 3 Maximize window horizontally. No default implementation.

* * *

### `Optional` minimizeWin

• **minimizeWin**? : _boolean_

_Defined in[xterm.d.ts:690](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L690)_

Ps=2 Iconify window. No default implementation.

* * *

### `Optional` popTitle

• **popTitle**? : _boolean_

_Defined in[xterm.d.ts:801](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L801)_

Ps=23 ; 0 Restore xterm icon and window title from stack. Ps=23 ; 1 Restore xterm icon title from stack. Ps=23 ; 2 Restore xterm window title from stack. All variants have a default implementation.

* * *

### `Optional` pushTitle

• **pushTitle**? : _boolean_

_Defined in[xterm.d.ts:794](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L794)_

Ps=22 ; 0 Save xterm icon and window title on stack. Ps=22 ; 1 Save xterm icon title on stack. Ps=22 ; 2 Save xterm window title on stack. All variants have a default implementation.

* * *

### `Optional` raiseWin

• **raiseWin**? : _boolean_

_Defined in[xterm.d.ts:709](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L709)_

Ps=5 Raise the window to the front of the stacking order. No default implementation.

* * *

### `Optional` refreshWin

• **refreshWin**? : _boolean_

_Defined in[xterm.d.ts:716](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L716)_

Ps=7 Refresh the window.

* * *

### `Optional` restoreWin

• **restoreWin**? : _boolean_

_Defined in[xterm.d.ts:685](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L685)_

Ps=1 De-iconify window. No default implementation.

* * *

### `Optional` setWinLines

• **setWinLines**? : _boolean_

_Defined in[xterm.d.ts:807](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L807)_

Ps>=24 Resize to Ps lines (DECSLPP). DECSLPP is not implemented. This settings is also used to enable / disable DECCOLM (earlier variant of DECSLPP).

* * *

### `Optional` setWinPosition

• **setWinPosition**? : _boolean_

_Defined in[xterm.d.ts:696](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L696)_

Ps=3 ; x ; y Move window to [x, y]. No default implementation.

* * *

### `Optional` setWinSizeChars

• **setWinSizeChars**? : _boolean_

_Defined in[xterm.d.ts:724](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L724)_

Ps = 8 ; height ; width Resize the text area to given height and width in characters. Omitted parameters should reuse the current height or width. Zero parameters use the display’s height or width. No default implementation.

* * *

### `Optional` setWinSizePixels

• **setWinSizePixels**? : _boolean_

_Defined in[xterm.d.ts:704](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L704)_

Ps = 4 ; height ; width Resize the window to given `height` and `width` in pixels. Omitted parameters should reuse the current height or width. Zero parameters should use the display’s height or width. No default implementation.