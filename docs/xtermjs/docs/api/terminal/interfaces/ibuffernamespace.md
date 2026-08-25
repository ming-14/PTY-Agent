# Interface: IBufferNamespace

Represents the terminal’s set of buffers.

## Hierarchy

  * **IBufferNamespace**


## Index

### Properties

  * [active](https://xtermjs.org/docs/api/terminal/interfaces/ibuffernamespace/#readonly-active)
  * [alternate](https://xtermjs.org/docs/api/terminal/interfaces/ibuffernamespace/#readonly-alternate)
  * [normal](https://xtermjs.org/docs/api/terminal/interfaces/ibuffernamespace/#readonly-normal)
  * [onBufferChange](https://xtermjs.org/docs/api/terminal/interfaces/ibuffernamespace/#onbufferchange)


## Properties

### `Readonly` active

• **active** : _[IBuffer](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/)_

_Defined in[xterm.d.ts:1573](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1573)_

The active buffer, this will either be the normal or alternate buffers.

* * *

### `Readonly` alternate

• **alternate** : _[IBuffer](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/)_

_Defined in[xterm.d.ts:1584](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1584)_

The alternate buffer, this becomes the active buffer when an application enters this mode via DECSET (`CSI ? 4 7 h`)

* * *

### `Readonly` normal

• **normal** : _[IBuffer](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/)_

_Defined in[xterm.d.ts:1578](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1578)_

The normal buffer.

* * *

### onBufferChange

• **onBufferChange** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹[IBuffer](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/)›_

_Defined in[xterm.d.ts:1590](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1590)_

Adds an event listener for when the active buffer changes.

**`returns`** an `IDisposable` to stop listening.