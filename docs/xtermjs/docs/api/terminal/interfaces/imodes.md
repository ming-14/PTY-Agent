# Interface: IModes

Terminal modes as set by SM/DECSET.

## Hierarchy

  * **IModes**


## Index

### Properties

  * [applicationCursorKeysMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-applicationcursorkeysmode)
  * [applicationKeypadMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-applicationkeypadmode)
  * [bracketedPasteMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-bracketedpastemode)
  * [insertMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-insertmode)
  * [mouseTrackingMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-mousetrackingmode)
  * [originMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-originmode)
  * [reverseWraparoundMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-reversewraparoundmode)
  * [sendFocusMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-sendfocusmode)
  * [synchronizedOutputMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-synchronizedoutputmode)
  * [wraparoundMode](https://xtermjs.org/docs/api/terminal/interfaces/imodes/#readonly-wraparoundmode)


## Properties

### `Readonly` applicationCursorKeysMode

• **applicationCursorKeysMode** : _boolean_

_Defined in[xterm.d.ts:1911](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1911)_

Application Cursor Keys (DECCKM): `CSI ? 1 h`

* * *

### `Readonly` applicationKeypadMode

• **applicationKeypadMode** : _boolean_

_Defined in[xterm.d.ts:1915](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1915)_

Application Keypad Mode (DECNKM): `CSI ? 6 6 h`

* * *

### `Readonly` bracketedPasteMode

• **bracketedPasteMode** : _boolean_

_Defined in[xterm.d.ts:1919](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1919)_

Bracketed Paste Mode: `CSI ? 2 0 0 4 h`

* * *

### `Readonly` insertMode

• **insertMode** : _boolean_

_Defined in[xterm.d.ts:1923](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1923)_

Insert Mode (IRM): `CSI 4 h`

* * *

### `Readonly` mouseTrackingMode

• **mouseTrackingMode** : *“none” | “x10” | “vt200” | “drag” | “any”*  
---|---|---|---|---  
  
_Defined in[xterm.d.ts:1932](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1932)_

Mouse Tracking, this can be one of the following:

  * none: This is the default value and can be reset with DECRST
  * x10: Send Mouse X & Y on button press `CSI ? 9 h`
  * vt200: Send Mouse X & Y on button press and release `CSI ? 1 0 0 0 h`
  * drag: Use Cell Motion Mouse Tracking `CSI ? 1 0 0 2 h`
  * any: Use All Motion Mouse Tracking `CSI ? 1 0 0 3 h`


* * *

### `Readonly` originMode

• **originMode** : _boolean_

_Defined in[xterm.d.ts:1936](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1936)_

Origin Mode (DECOM): `CSI ? 6 h`

* * *

### `Readonly` reverseWraparoundMode

• **reverseWraparoundMode** : _boolean_

_Defined in[xterm.d.ts:1940](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1940)_

Reverse-wraparound Mode: `CSI ? 4 5 h`

* * *

### `Readonly` sendFocusMode

• **sendFocusMode** : _boolean_

_Defined in[xterm.d.ts:1944](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1944)_

Send FocusIn/FocusOut events: `CSI ? 1 0 0 4 h`

* * *

### `Readonly` synchronizedOutputMode

• **synchronizedOutputMode** : _boolean_

_Defined in[xterm.d.ts:1951](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1951)_

Synchronized Output Mode: `CSI ? 2 0 2 6 h`

When enabled, output is buffered and only rendered when the mode is disabled, allowing for atomic screen updates without tearing.

* * *

### `Readonly` wraparoundMode

• **wraparoundMode** : _boolean_

_Defined in[xterm.d.ts:1955](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1955)_

Auto-Wrap Mode (DECAWM): `CSI ? 7 h`