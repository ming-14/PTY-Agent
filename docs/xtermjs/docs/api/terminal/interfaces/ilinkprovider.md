# Interface: ILinkProvider

A custom link provider.

## Hierarchy

  * **ILinkProvider**


## Index

### Methods

  * [provideLinks](https://xtermjs.org/docs/api/terminal/interfaces/ilinkprovider/#providelinks)


## Methods

### provideLinks

▸ **provideLinks**(`bufferLineNumber`: number, `callback`: function): _void_

_Defined in[xterm.d.ts:1401](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1401)_

Provides a link a buffer position

**Parameters:**

▪ **bufferLineNumber** : _number_

The y position of the buffer to check for links within.

▪ **callback** : _function_

The callback to be fired when ready with the resulting link(s) for the line or `undefined`.

▸ (`links`: [ILink](https://xtermjs.org/docs/api/terminal/interfaces/ilink/)[] | undefined): _void_  
---|---  
  
**Parameters:**

Name | Type  
---|---  
`links` | [ILink](https://xtermjs.org/docs/api/terminal/interfaces/ilink/)[] | undefined  
  
**Returns:** _void_