# Interface: IDisposableWithEvent

Represents a disposable that tracks is disposed state.

## Hierarchy

  * [IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)

↳ **IDisposableWithEvent**

↳ [IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)

↳ [IDecoration](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/)


## Index

### Properties

  * [isDisposed](https://xtermjs.org/docs/api/terminal/interfaces/idisposablewithevent/#readonly-isdisposed)
  * [onDispose](https://xtermjs.org/docs/api/terminal/interfaces/idisposablewithevent/#ondispose)


### Methods

  * [dispose](https://xtermjs.org/docs/api/terminal/interfaces/idisposablewithevent/#dispose)


## Properties

### `Readonly` isDisposed

• **isDisposed** : _boolean_

_Defined in[xterm.d.ts:508](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L508)_

Whether this is disposed.

* * *

### onDispose

• **onDispose** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Defined in[xterm.d.ts:503](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L503)_

Event listener to get notified when this gets disposed.

## Methods

### dispose

▸ **dispose**(): _void_

_Inherited from[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/).[dispose](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/#dispose)_

_Defined in[xterm.d.ts:467](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L467)_

**Returns:** _void_