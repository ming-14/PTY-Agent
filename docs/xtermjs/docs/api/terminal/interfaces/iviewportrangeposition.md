# Interface: IViewportRangePosition

An object representing a cell position within the viewport of the terminal.

## Hierarchy

  * **IViewportRangePosition**


## Index

### Properties

  * [x](https://xtermjs.org/docs/api/terminal/interfaces/iviewportrangeposition/#x)
  * [y](https://xtermjs.org/docs/api/terminal/interfaces/iviewportrangeposition/#y)


## Properties

### x

• **x** : _number_

_Defined in[xterm.d.ts:1341](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1341)_

The x position of the cell. This is a 0-based index that refers to the space in between columns, not the column itself. Index 0 refers to the left side of the viewport, index `Terminal.cols` refers to the right side of the viewport. This can be thought of as how a cursor is positioned in a text editor.

* * *

### y

• **y** : _number_

_Defined in[xterm.d.ts:1347](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1347)_

The y position of the cell. This is a 0-based index that refers to a specific row.