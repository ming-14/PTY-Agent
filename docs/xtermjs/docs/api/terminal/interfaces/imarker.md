# Interface: IMarker

Represents a specific line in the terminal that is tracked when scrollback is trimmed and lines are added or removed. This is a single line that may be part of a larger wrapped line.

## Hierarchy

↳ [IDisposableWithEvent](https://xtermjs.org/docs/api/terminal/interfaces/idisposablewithevent/)

↳ **IMarker**

## Index

### Properties

  * [id](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#readonly-id)
  * [isDisposed](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#readonly-isdisposed)
  * [line](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#readonly-line)
  * [onDispose](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#ondispose)


### Methods

  * [dispose](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#dispose)


## Properties

### `Readonly` id

• **id** : _number_

_Defined in[xterm.d.ts:487](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L487)_

A unique identifier for this marker.

* * *

### `Readonly` isDisposed

• **isDisposed** : _boolean_

_Inherited from[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/).[isDisposed](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#readonly-isdisposed)_

_Defined in[xterm.d.ts:508](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L508)_

Whether this is disposed.

* * *

### `Readonly` line

• **line** : _number_

_Defined in[xterm.d.ts:493](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L493)_

The actual line index in the buffer at this point in time. This is set to -1 if the marker has been disposed.

* * *

### onDispose

• **onDispose** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Inherited from[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/).[onDispose](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#ondispose)_

_Defined in[xterm.d.ts:503](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L503)_

Event listener to get notified when this gets disposed.

## Methods

### dispose

▸ **dispose**(): _void_

_Inherited from[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/).[dispose](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/#dispose)_

_Defined in[xterm.d.ts:467](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L467)_

**Returns:** _void_