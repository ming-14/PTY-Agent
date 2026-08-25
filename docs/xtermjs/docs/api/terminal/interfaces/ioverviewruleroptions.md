# Interface: IOverviewRulerOptions

## Hierarchy

  * **IOverviewRulerOptions**


## Index

### Properties

  * [showBottomBorder](https://xtermjs.org/docs/api/terminal/interfaces/ioverviewruleroptions/#optional-showbottomborder)
  * [showTopBorder](https://xtermjs.org/docs/api/terminal/interfaces/ioverviewruleroptions/#optional-showtopborder)
  * [width](https://xtermjs.org/docs/api/terminal/interfaces/ioverviewruleroptions/#optional-width)


## Properties

### `Optional` showBottomBorder

• **showBottomBorder**? : _boolean_

_Defined in[xterm.d.ts:650](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L650)_

Whether to show the bottom border of the overview ruler, which uses the [ITheme.overviewRulerBorder](https://xtermjs.org/docs/api/terminal/interfaces/itheme/#optional-overviewrulerborder) color.

* * *

### `Optional` showTopBorder

• **showTopBorder**? : _boolean_

_Defined in[xterm.d.ts:644](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L644)_

Whether to show the top border of the overview ruler, which uses the [ITheme.overviewRulerBorder](https://xtermjs.org/docs/api/terminal/interfaces/itheme/#optional-overviewrulerborder) color.

* * *

### `Optional` width

• **width**? : _number_

_Defined in[xterm.d.ts:638](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L638)_

When defined, renders decorations in the overview ruler to the right of the terminal. This must be set in order to see the overview ruler.

**`param`** The color of the decoration.

**`param`** The position of the decoration.