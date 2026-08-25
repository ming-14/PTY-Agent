# Interface: ILinkHandler

A link handler for OSC 8 hyperlinks.

## Hierarchy

  * **ILinkHandler**


## Index

### Properties

  * [allowNonHttpProtocols](https://xtermjs.org/docs/api/terminal/interfaces/ilinkhandler/#optional-allownonhttpprotocols)


### Methods

  * [activate](https://xtermjs.org/docs/api/terminal/interfaces/ilinkhandler/#activate)
  * [hover](https://xtermjs.org/docs/api/terminal/interfaces/ilinkhandler/#optional-hover)
  * [leave](https://xtermjs.org/docs/api/terminal/interfaces/ilinkhandler/#optional-leave)


## Properties

### `Optional` allowNonHttpProtocols

• **allowNonHttpProtocols**? : _boolean_

_Defined in[xterm.d.ts:1387](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1387)_

Whether to receive non-HTTP URLs from LinkProvider. When false, any usage of non-HTTP URLs will be ignored. Enabling this option without proper protection in `activate` function may cause security issues such as XSS.

## Methods

### activate

▸ **activate**(`event`: MouseEvent, `text`: string, `range`: [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/)): _void_

_Defined in[xterm.d.ts:1360](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1360)_

Calls when the link is activated.

**Parameters:**

Name | Type | Description  
---|---|---  
`event` | MouseEvent | The mouse event triggering the callback.  
`text` | string | The text of the link.  
`range` | [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/) | The buffer range of the link.  
  
**Returns:** _void_

* * *

### `Optional` hover

▸ **hover**(`event`: MouseEvent, `text`: string, `range`: [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/)): _void_

_Defined in[xterm.d.ts:1371](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1371)_

Called when the mouse hovers the link. To use this to create a DOM-based hover tooltip, create the hover element within `Terminal.element` and add the `xterm-hover` class to it, that will cause mouse events to not fall through and activate other links.

**Parameters:**

Name | Type | Description  
---|---|---  
`event` | MouseEvent | The mouse event triggering the callback.  
`text` | string | The text of the link.  
`range` | [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/) | The buffer range of the link.  
  
**Returns:** _void_

* * *

### `Optional` leave

▸ **leave**(`event`: MouseEvent, `text`: string, `range`: [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/)): _void_

_Defined in[xterm.d.ts:1379](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1379)_

Called when the mouse leaves the link.

**Parameters:**

Name | Type | Description  
---|---|---  
`event` | MouseEvent | The mouse event triggering the callback.  
`text` | string | The text of the link.  
`range` | [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/) | The buffer range of the link.  
  
**Returns:** _void_