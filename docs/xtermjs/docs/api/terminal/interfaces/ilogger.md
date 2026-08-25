# Interface: ILogger

A replacement logger for `console`.

## Hierarchy

  * **ILogger**


## Index

### Methods

  * [debug](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/#debug)
  * [error](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/#error)
  * [info](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/#info)
  * [trace](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/#trace)
  * [warn](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/#warn)


## Methods

### debug

▸ **debug**(`message`: string, …`args`: any[]): _void_

_Defined in[xterm.d.ts:445](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L445)_

Log a debug message, this will only be called if [ITerminalOptions.logLevel](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-loglevel) is set to debug or below.

**Parameters:**

Name | Type  
---|---  
`message` | string  
`...args` | any[]  
  
**Returns:** _void_

* * *

### error

▸ **error**(`message`: string | Error, …`args`: any[]): _void_  
---|---  
  
_Defined in[xterm.d.ts:460](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L460)_

Log a debug message, this will only be called if [ITerminalOptions.logLevel](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-loglevel) is set to error or below.

**Parameters:**

Name | Type  
---|---  
`message` | string | Error  
`...args` | any[]  
  
**Returns:** _void_

* * *

### info

▸ **info**(`message`: string, …`args`: any[]): _void_

_Defined in[xterm.d.ts:450](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L450)_

Log a debug message, this will only be called if [ITerminalOptions.logLevel](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-loglevel) is set to info or below.

**Parameters:**

Name | Type  
---|---  
`message` | string  
`...args` | any[]  
  
**Returns:** _void_

* * *

### trace

▸ **trace**(`message`: string, …`args`: any[]): _void_

_Defined in[xterm.d.ts:440](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L440)_

Log a trace message, this will only be called if [ITerminalOptions.logLevel](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-loglevel) is set to trace.

**Parameters:**

Name | Type  
---|---  
`message` | string  
`...args` | any[]  
  
**Returns:** _void_

* * *

### warn

▸ **warn**(`message`: string, …`args`: any[]): _void_

_Defined in[xterm.d.ts:455](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L455)_

Log a debug message, this will only be called if [ITerminalOptions.logLevel](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-loglevel) is set to warn or below.

**Parameters:**

Name | Type  
---|---  
`message` | string  
`...args` | any[]  
  
**Returns:** _void_