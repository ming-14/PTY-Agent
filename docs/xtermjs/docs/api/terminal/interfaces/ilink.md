# Interface: ILink

A link within the terminal.

## Hierarchy

  * **ILink**


## Index

### Properties

  * [decorations](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#optional-decorations)
  * [range](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#range)
  * [text](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#text)


### Methods

  * [activate](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#activate)
  * [dispose](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#optional-dispose)
  * [hover](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#optional-hover)
  * [leave](https://xtermjs.org/docs/api/terminal/interfaces/ilink/#optional-leave)


## Properties

### `Optional` decorations

• **decorations**? : _[ILinkDecorations](https://xtermjs.org/docs/api/terminal/interfaces/ilinkdecorations/)_

_Defined in[xterm.d.ts:1423](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1423)_

What link decorations to show when hovering the link, this property is tracked and changes made after the link is provided will trigger changes. If not set, all decroations will be enabled.

* * *

### range

• **range** : _[IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/)_

_Defined in[xterm.d.ts:1411](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1411)_

The buffer range of the link.

* * *

### text

• **text** : _string_

_Defined in[xterm.d.ts:1416](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1416)_

The text of the link.

## Methods

### activate

▸ **activate**(`event`: MouseEvent, `text`: string): _void_

_Defined in[xterm.d.ts:1430](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1430)_

Calls when the link is activated.

**Parameters:**

Name | Type | Description  
---|---|---  
`event` | MouseEvent | The mouse event triggering the callback.  
`text` | string | The text of the link.  
  
**Returns:** _void_

* * *

### `Optional` dispose

▸ **dispose**(): _void_

_Defined in[xterm.d.ts:1452](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1452)_

Called when the link is released and no longer used by xterm.js.

**Returns:** _void_

* * *

### `Optional` hover

▸ **hover**(`event`: MouseEvent, `text`: string): _void_

_Defined in[xterm.d.ts:1440](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1440)_

Called when the mouse hovers the link. To use this to create a DOM-based hover tooltip, create the hover element within `Terminal.element` and add the `xterm-hover` class to it, that will cause mouse events to not fall through and activate other links.

**Parameters:**

Name | Type | Description  
---|---|---  
`event` | MouseEvent | The mouse event triggering the callback.  
`text` | string | The text of the link.  
  
**Returns:** _void_

* * *

### `Optional` leave

▸ **leave**(`event`: MouseEvent, `text`: string): _void_

_Defined in[xterm.d.ts:1447](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1447)_

Called when the mouse leaves the link.

**Parameters:**

Name | Type | Description  
---|---|---  
`event` | MouseEvent | The mouse event triggering the callback.  
`text` | string | The text of the link.  
  
**Returns:** _void_