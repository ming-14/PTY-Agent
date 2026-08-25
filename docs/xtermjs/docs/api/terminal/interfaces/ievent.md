# Interface: IEvent ‹T, U›

An event that can be listened to.

## Type parameters

▪ **T**

▪ **U**

## Hierarchy

  * **IEvent**


## Callable

▸ (`listener`: function): _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:474](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L474)_

An event that can be listened to.

**Parameters:**

▪ **listener** : _function_

▸ (`arg1`: T, `arg2`: U): _any_

**Parameters:**

Name | Type  
---|---  
`arg1` | T  
`arg2` | U  
  
**Returns:** _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

an `IDisposable` to stop listening.