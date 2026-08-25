# Interface: IParser

Allows hooking into the parser for custom handling of escape sequences.

Note on sync vs. async handlers: xterm.js implements all parser actions with synchronous handlers. In general custom handlers should also operate in sync mode wherever possible to keep the parser fast. Still the exposed interfaces allow to register async handlers by returning a `Promise<boolean>`. Here the parser will pause input processing until the promise got resolved or rejected (in-band blocking). This “full stop” on the input chain allows to implement backpressure from a certain async action while the terminal state will not progress any further from input. It does not mean that the terminal state will not change at all in between, as user actions like resize or reset are still processed immediately. It is an error to assume a stable terminal state while giving back control in between, e.g. by multiple chained `then` calls. Downside of an async handler is a rather bad throughput performance, thus use async handlers only as a last resort or for actions that have to rely on async interfaces itself.

## Hierarchy

  * **IParser**


## Index

### Methods

  * [registerCsiHandler](https://xtermjs.org/docs/api/terminal/interfaces/iparser/#registercsihandler)
  * [registerDcsHandler](https://xtermjs.org/docs/api/terminal/interfaces/iparser/#registerdcshandler)
  * [registerEscHandler](https://xtermjs.org/docs/api/terminal/interfaces/iparser/#registereschandler)
  * [registerOscHandler](https://xtermjs.org/docs/api/terminal/interfaces/iparser/#registeroschandler)


## Methods

### registerCsiHandler

▸ **registerCsiHandler**(`id`: [IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/), `callback`: function): _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:1817](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1817)_

Adds a handler for CSI escape sequences.

**Parameters:**

▪ **id** : _[IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/)_

Specifies the function identifier under which the callback gets registered, e.g. {final: ‘m’} for SGR.

▪ **callback** : _function_

The function to handle the sequence. The callback is called with the numerical params. If the sequence has subparams the array will contain subarrays with their numercial values. Return `true` if the sequence was handled, `false` if the parser should try a previous handler. The most recently added handler is tried first.

▸ (`params`: (number | number[])[]): *boolean | Promise‹boolean›*  
---|---|---  
  
**Parameters:**

Name | Type  
---|---  
`params` | (number | number[])[]  
  
**Returns:** _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

An IDisposable you can call to remove this handler.

* * *

### registerDcsHandler

▸ **registerDcsHandler**(`id`: [IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/), `callback`: function): _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:1835](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1835)_

Adds a handler for DCS escape sequences.

**Parameters:**

▪ **id** : _[IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/)_

Specifies the function identifier under which the callback gets registered, e.g. {intermediates: ‘$’ final: ‘q’} for DECRQSS.

▪ **callback** : _function_

The function to handle the sequence. Note that the function will only be called once if the sequence finished sucessfully. There is currently no way to intercept smaller data chunks, data chunks will be stored up until the sequence is finished. Since DCS sequences are not limited by the amount of data this might impose a problem for big payloads. Currently xterm.js limits DCS payload to 10 MB which should give enough room for most use cases. The function gets the payload and numerical parameters as arguments. Return `true` if the sequence was handled, `false` if the parser should try a previous handler. The most recently added handler is tried first.

▸ (`data`: string, `param`: (number | number[])[]): *boolean | Promise‹boolean›*  
---|---|---  
  
**Parameters:**

Name | Type  
---|---  
`data` | string  
`param` | (number | number[])[]  
  
**Returns:** _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

An IDisposable you can call to remove this handler.

* * *

### registerEscHandler

▸ **registerEscHandler**(`id`: [IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/), `handler`: function): _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:1847](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1847)_

Adds a handler for ESC escape sequences.

**Parameters:**

▪ **id** : _[IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/)_

Specifies the function identifier under which the callback gets registered, e.g. {intermediates: ‘%’ final: ‘G’} for default charset selection.

▪ **handler** : _function_

▸ (): *boolean | Promise‹boolean›*  
---|---  
  
**Returns:** _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

An IDisposable you can call to remove this handler.

* * *

### registerOscHandler

▸ **registerOscHandler**(`ident`: number, `callback`: function): _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:1864](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1864)_

Adds a handler for OSC escape sequences.

**Parameters:**

▪ **ident** : _number_

The number (first parameter) of the sequence.

▪ **callback** : _function_

The function to handle the sequence. Note that the function will only be called once if the sequence finished sucessfully. There is currently no way to intercept smaller data chunks, data chunks will be stored up until the sequence is finished. Since OSC sequences are not limited by the amount of data this might impose a problem for big payloads. Currently xterm.js limits OSC payload to 10 MB which should give enough room for most use cases. The callback is called with OSC data string. Return `true` if the sequence was handled, `false` if the parser should try a previous handler. The most recently added handler is tried first.

▸ (`data`: string): *boolean | Promise‹boolean›*  
---|---  
  
**Parameters:**

Name | Type  
---|---  
`data` | string  
  
**Returns:** _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

An IDisposable you can call to remove this handler.