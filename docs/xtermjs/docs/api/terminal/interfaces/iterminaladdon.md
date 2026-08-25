# Interface: ITerminalAddon

An addon that can provide additional functionality to the terminal.

## Hierarchy

  * [IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)

↳ **ITerminalAddon**


## Index

### Methods

  * [activate](https://xtermjs.org/docs/api/terminal/interfaces/iterminaladdon/#activate)
  * [dispose](https://xtermjs.org/docs/api/terminal/interfaces/iterminaladdon/#dispose)


## Methods

### activate

▸ **activate**(`terminal`: [Terminal](https://xtermjs.org/docs/api/terminal/classes/terminal/)): _void_

_Defined in[xterm.d.ts:1312](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1312)_

This is called when the addon is activated.

**Parameters:**

Name | Type  
---|---  
`terminal` | [Terminal](https://xtermjs.org/docs/api/terminal/classes/terminal/)  
  
**Returns:** _void_

* * *

### dispose

▸ **dispose**(): _void_

_Inherited from[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/).[dispose](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/#dispose)_

_Defined in[xterm.d.ts:467](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L467)_

**Returns:** _void_