# Interface: IDecorationOptions

## Hierarchy

  * **IDecorationOptions**


## Index

### Properties

  * [anchor](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-anchor)
  * [backgroundColor](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-backgroundcolor)
  * [foregroundColor](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-foregroundcolor)
  * [height](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-height)
  * [layer](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-layer)
  * [marker](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#readonly-marker)
  * [overviewRulerOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-overviewruleroptions)
  * [width](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-width)
  * [x](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-x)


## Properties

### `Optional` `Readonly` anchor

• **anchor**? : *“right” | “left”*  
---|---  
  
_Defined in[xterm.d.ts:566](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L566)_

* * *

### `Optional` `Readonly` backgroundColor

• **backgroundColor**? : _string_

_Defined in[xterm.d.ts:589](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L589)_

The background color of the cell(s). When 2 decorations both set the foreground color the last registered decoration will be used. Only the `#RRGGBB` format is supported.

* * *

### `Optional` `Readonly` foregroundColor

• **foregroundColor**? : _string_

_Defined in[xterm.d.ts:596](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L596)_

The foreground color of the cell(s). When 2 decorations both set the foreground color the last registered decoration will be used. Only the `#RRGGBB` format is supported.

* * *

### `Optional` `Readonly` height

• **height**? : _number_

_Defined in[xterm.d.ts:582](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L582)_

The height of the decoration in cells, defaults to 1.

* * *

### `Optional` `Readonly` layer

• **layer**? : *“bottom” | “top”*  
---|---  
  
_Defined in[xterm.d.ts:603](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L603)_

What layer to render the decoration at when [backgroundColor](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-backgroundcolor) or [foregroundColor](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-readonly-foregroundcolor) are used. `'bottom'` will render under the selection, `'top`’ will render above the selection*.

* * *

### `Readonly` marker

• **marker** : _[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)_

_Defined in[xterm.d.ts:560](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L560)_

The line in the terminal where the decoration will be displayed

* * *

### `Optional` overviewRulerOptions

• **overviewRulerOptions**? : _[IDecorationOverviewRulerOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoverviewruleroptions/)_

_Defined in[xterm.d.ts:612](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L612)_

When defined, renders the decoration in the overview ruler to the right of the terminal. [IOverviewRulerOptions.width](https://xtermjs.org/docs/api/terminal/interfaces/ioverviewruleroptions/#optional-width) must be set in order to see the overview ruler.

**`param`** The color of the decoration.

**`param`** The position of the decoration.

* * *

### `Optional` `Readonly` width

• **width**? : _number_

_Defined in[xterm.d.ts:577](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L577)_

The width of the decoration in cells, defaults to 1.

* * *

### `Optional` `Readonly` x

• **x**? : _number_

_Defined in[xterm.d.ts:571](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L571)_

The x position offset relative to the anchor