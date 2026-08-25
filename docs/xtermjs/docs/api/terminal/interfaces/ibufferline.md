# Interface: IBufferLine

Represents a line in the terminal’s buffer.

## Hierarchy

  * **IBufferLine**


## Index

### Properties

  * [isWrapped](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/#readonly-iswrapped)
  * [length](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/#readonly-length)


### Methods

  * [getCell](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/#getcell)
  * [translateToString](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/#translatetostring)


## Properties

### `Readonly` isWrapped

• **isWrapped** : _boolean_

_Defined in[xterm.d.ts:1600](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1600)_

Whether the line is wrapped from the previous line.

* * *

### `Readonly` length

• **length** : _number_

_Defined in[xterm.d.ts:1608](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1608)_

The length of the line, all call to getCell beyond the length will result in `undefined`. Note that this may exceed columns as the line array may not be trimmed after a resize, compare against [Terminal.cols](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-cols) to get the actual maximum length of a line.

## Methods

### getCell

▸ **getCell**(`x`: number, `cell?`: [IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/)): *[IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/) | undefined*  
---|---  
  
_Defined in[xterm.d.ts:1622](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1622)_

Gets a cell from the line, or undefined if the line index does not exist.

Note that the result of this function should be used immediately after calling as when the terminal updates it could lead to unexpected behavior.

**Parameters:**

Name | Type | Description  
---|---|---  
`x` | number | The character index to get.  
`cell?` | [IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/) | Optional cell object to load data into for performance reasons. This is mainly useful when every cell in the buffer is being looped over to avoid creating new objects for every cell.  
**Returns:** *[IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/) | undefined*  
---|---  
  
* * *

### translateToString

▸ **translateToString**(`trimRight?`: boolean, `startColumn?`: number, `endColumn?`: number): _string_

_Defined in[xterm.d.ts:1632](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1632)_

Gets the line as a string. Note that this is gets only the string for the line, not taking isWrapped into account.

**Parameters:**

Name | Type | Description  
---|---|---  
`trimRight?` | boolean | Whether to trim any whitespace at the right of the line.  
`startColumn?` | number | The column to start from (inclusive).  
`endColumn?` | number | The column to end at (exclusive).  
  
**Returns:** _string_