# Interface: IBuffer

Represents a terminal buffer.

## Hierarchy

  * **IBuffer**


## Index

### Properties

  * [baseY](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#readonly-basey)
  * [cursorX](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#readonly-cursorx)
  * [cursorY](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#readonly-cursory)
  * [length](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#readonly-length)
  * [type](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#readonly-type)
  * [viewportY](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#readonly-viewporty)


### Methods

  * [getLine](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#getline)
  * [getNullCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/#getnullcell)


## Properties

### `Readonly` baseY

• **baseY** : _number_

_Defined in[xterm.d.ts:1531](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1531)_

The line within the buffer where the top of the bottom page is (when fully scrolled down).

* * *

### `Readonly` cursorX

• **cursorX** : _number_

_Defined in[xterm.d.ts:1520](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1520)_

The x position of the cursor. This ranges between `0` (left side) and `Terminal.cols` (after last cell of the row).

* * *

### `Readonly` cursorY

• **cursorY** : _number_

_Defined in[xterm.d.ts:1514](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1514)_

The y position of the cursor. This ranges between `0` (when the cursor is at baseY) and `Terminal.rows - 1` (when the cursor is on the last row).

* * *

### `Readonly` length

• **length** : _number_

_Defined in[xterm.d.ts:1536](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1536)_

The amount of lines in the buffer.

* * *

### `Readonly` type

• **type** : *“normal” | “alternate”*  
---|---  
  
_Defined in[xterm.d.ts:1507](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1507)_

The type of the buffer.

* * *

### `Readonly` viewportY

• **viewportY** : _number_

_Defined in[xterm.d.ts:1525](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1525)_

The line within the buffer where the top of the viewport is.

## Methods

### getLine

▸ **getLine**(`y`: number): *[IBufferLine](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/) | undefined*  
---|---  
  
_Defined in[xterm.d.ts:1548](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1548)_

Gets a line from the buffer, or undefined if the line index does not exist.

Note that the result of this function should be used immediately after calling as when the terminal updates it could lead to unexpected behavior.

**Parameters:**

Name | Type | Description  
---|---|---  
`y` | number | The line index to get.  
**Returns:** *[IBufferLine](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/) | undefined*  
---|---  
  
* * *

### getNullCell

▸ **getNullCell**(): _[IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/)_

_Defined in[xterm.d.ts:1555](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1555)_

Creates an empty cell object suitable as a cell reference in `line.getCell(x, cell)`. Use this to avoid costly recreation of cell objects when dealing with tons of cells.

**Returns:** _[IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/)_