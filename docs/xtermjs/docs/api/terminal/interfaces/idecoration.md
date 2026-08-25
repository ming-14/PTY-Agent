# Interface: IDecoration

Represents a decoration in the terminal that is associated with a particular marker and DOM element.

## Hierarchy

↳ [IDisposableWithEvent](https://xtermjs.org/docs/api/terminal/interfaces/idisposablewithevent/)

↳ **IDecoration**

## Index

### Properties

  * [element](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#element)
  * [isDisposed](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#readonly-isdisposed)
  * [marker](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#readonly-marker)
  * [onDispose](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#ondispose)
  * [onRender](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#readonly-onrender)
  * [options](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#options)


### Methods

  * [dispose](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#dispose)


## Properties

### element

• **element** : *HTMLElement | undefined*  
---|---  
  
_Defined in[xterm.d.ts:533](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L533)_

The element that the decoration is rendered to. This will be undefined until it is rendered for the first time by [IDecoration.onRender](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/#readonly-onrender). that.

* * *

### `Readonly` isDisposed

• **isDisposed** : _boolean_

_Inherited from[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/).[isDisposed](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#readonly-isdisposed)_

_Defined in[xterm.d.ts:508](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L508)_

Whether this is disposed.

* * *

### `Readonly` marker

• **marker** : _[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)_

_Defined in[xterm.d.ts:519](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L519)_

* * *

### onDispose

• **onDispose** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Inherited from[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/).[onDispose](https://xtermjs.org/docs/api/terminal/interfaces/imarker/#ondispose)_

_Defined in[xterm.d.ts:503](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L503)_

Event listener to get notified when this gets disposed.

* * *

### `Readonly` onRender

• **onRender** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹HTMLElement›_

_Defined in[xterm.d.ts:526](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L526)_

An event fired when the decoration is rendered, returns the dom element associated with the decoration.

* * *

### options

• **options** : _Pick‹[IDecorationOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/), “overviewRulerOptions”›_

_Defined in[xterm.d.ts:540](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L540)_

The options for the overview ruler that can be updated. This will only take effect when [IDecorationOptions.overviewRulerOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/#optional-overviewruleroptions) were provided initially.

## Methods

### dispose

▸ **dispose**(): _void_

_Inherited from[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/).[dispose](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/#dispose)_

_Defined in[xterm.d.ts:467](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L467)_

**Returns:** _void_